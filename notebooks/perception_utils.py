"""Helpers for the text-sentiment version of the perceived-inflation index.

Source-agnostic: any DataFrame/JSONL with columns id, created_utc, title, sub works.

Pre-registered design (decided BEFORE looking at the Reddit data):
  PRIMARY   measure: monthly mean "tone" = P(negative) - P(positive) from a
            sentiment model, averaged within each subreddit first, then across
            subreddits (so shifts in subreddit mix do not drive the index).
  SECONDARY measure: monthly mean price-direction score (rising words minus
            falling words), same averaging.
"""
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

START, END = "2021-01-01", "2026-09-01"

PRICE_RE = re.compile(r"\b(?:inflation|prices?|groceries|grocery|rent)\b", re.I)

RISE = {"up", "rise", "rises", "rising", "rose", "surge", "surges", "surged",
        "surging", "soar", "soars", "soared", "soaring", "spike", "spikes",
        "spiked", "jump", "jumps", "jumped", "climb", "climbs", "climbed",
        "hike", "hikes", "hiked", "skyrocket", "skyrockets", "skyrocketed",
        "increase", "increases", "increased", "increasing", "higher", "worsen",
        "worsens", "worsening", "doubled", "tripled"}
FALL = {"down", "fall", "falls", "fell", "falling", "drop", "drops", "dropped",
        "dropping", "decline", "declines", "declined", "declining", "cool",
        "cools", "cooled", "cooling", "ease", "eases", "eased", "easing",
        "slow", "slows", "slowed", "slowing", "lower", "cut", "cuts",
        "decrease", "decreases", "decreased", "plunge", "plunged", "tumble",
        "tumbled", "retreat", "retreats", "retreated"}
_WORD = re.compile(r"[a-z']+")


# ---------- loading and cleaning ----------
def load_titles(path):
    """Read the collector's JSONL, dedupe, parse dates, keep the study window."""
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.drop_duplicates(subset="id").copy()
    ts = df["created_utc"]
    if pd.api.types.is_numeric_dtype(ts):
        df["date"] = pd.to_datetime(ts, unit="s", errors="coerce")
    else:
        df["date"] = pd.to_datetime(ts, utc=True, errors="coerce").dt.tz_localize(None)
    df = df.dropna(subset=["date", "title"])
    df["title"] = df["title"].astype(str).str.strip()
    bad = df["title"].str.lower().isin(["", "[deleted]", "[removed]"])
    df = df[~bad]
    df = df[(df["date"] >= START) & (df["date"] < pd.Timestamp(END) + pd.offsets.MonthBegin(1))]
    df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()
    # identical titles within the same month and subreddit are usually reposts
    df = df.drop_duplicates(subset=["sub", "month", "title"])
    # the server's title search may be fuzzy, so re-check the keywords ourselves
    df = df[df["title"].str.contains(PRICE_RE)]
    return df.reset_index(drop=True)


def coverage(df, min_n=30):
    """Titles per month per subreddit, so thin months are visible."""
    tab = df.pivot_table(index="month", columns="sub", values="id", aggfunc="count").fillna(0).astype(int)
    subs = [c for c in tab.columns]
    tab["subs_ok"] = (tab[subs] >= 10).sum(axis=1)   # subreddits with enough titles
    tab["total"] = tab[subs].sum(axis=1)
    tab["thin"] = (tab["total"] < min_n) | (tab["subs_ok"] < 2)
    return tab


# ---------- scoring ----------
def direction_score(title):
    """+1 if more rising than falling words, -1 if more falling, else 0."""
    words = _WORD.findall(title.lower())
    rise = sum(w in RISE for w in words)
    fall = sum(w in FALL for w in words)
    return int(np.sign(rise - fall))


def score_titles(df, clf, batch_size=64, cache=None):
    """Add p_neg, p_neu, p_pos, tone and direction. `clf` is a transformers
    pipeline built with top_k=None. Results are cached as a pickle file if asked."""
    if cache and Path(cache).exists():
        cached = pd.read_pickle(cache)
        if set(df["id"]) <= set(cached["id"]):
            return cached[cached["id"].isin(df["id"])].reset_index(drop=True)
    outs = clf(df["title"].tolist(), batch_size=batch_size)
    probs = pd.DataFrame([{d["label"].lower(): d["score"] for d in o} for o in outs])
    res = df.reset_index(drop=True).copy()
    res["p_neg"] = probs["negative"].values
    res["p_neu"] = probs["neutral"].values
    res["p_pos"] = probs["positive"].values
    res["tone"] = res["p_neg"] - res["p_pos"]
    res["direction"] = res["title"].map(direction_score)
    if cache:
        res.to_pickle(cache)
    return res


# ---------- monthly index ----------
def monthly_index(scored, min_cell=10):
    """Average within subreddit-month cells first, then across subreddits."""
    cell = (scored.groupby(["month", "sub"])
            .agg(n=("tone", "size"), tone=("tone", "mean"), direction=("direction", "mean"))
            .reset_index())
    cell = cell[cell["n"] >= min_cell]
    idx = cell.groupby("month").agg(subs=("sub", "nunique"), n=("n", "sum"),
                                    tone=("tone", "mean"), direction=("direction", "mean"))
    full = pd.date_range(START, END, freq="MS")
    idx = idx.reindex(full)
    idx.index.name = "date"
    for c in ["tone", "direction"]:
        idx[c + "_z"] = (idx[c] - idx[c].mean()) / idx[c].std()
    return idx


# ---------- joining and lag analysis ----------
def merge_all(idx, data_dir="../data"):
    """Join the text index with CPI, Michigan, and the Google Trends index."""
    off = pd.read_csv(f"{data_dir}/official.csv", index_col=0, parse_dates=True)
    tr = pd.read_csv(f"{data_dir}/trends.csv", index_col=0, parse_dates=True)
    tr = tr.resample("MS").mean()
    out = idx.join(off, how="left")
    out["search_z"] = ((tr["inflation"] - tr["inflation"].mean()) / tr["inflation"].std()).reindex(out.index)
    return out.reindex(pd.date_range(START, END, freq="MS"))


def lagtable(df, col, targets=("cpi_yoy", "mich_expect", "search_z"), maxlag=6, diff=False):
    """Correlation of col shifted by k months (positive k = col leads target)."""
    a = df.diff() if diff else df
    rows = []
    for k in range(maxlag + 1):
        row = {"lag": k}
        for t in targets:
            row[t] = a[col].shift(k).corr(a[t])
        rows.append(row)
    return pd.DataFrame(rows).set_index("lag").round(2)


def reverse_lagtable(df, col, targets=("cpi_yoy", "mich_expect", "search_z"), maxlag=6):
    """Correlation when the TARGET leads col by k months (reverse direction)."""
    rows = []
    for k in range(1, maxlag + 1):
        row = {"target_leads_by": k}
        for t in targets:
            row[t] = df[col].corr(df[t].shift(k))
        rows.append(row)
    return pd.DataFrame(rows).set_index("target_leads_by").round(2)
