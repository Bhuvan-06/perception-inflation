Question: does public sentiment about prices move before official inflation does.
Country: United States.
Window: January 2021 to the latest month available.
Out of scope: India, causation claims, dashboards.

## Pre-registered rules for the text version
Primary measure: monthly mean tone (P(negative) minus P(positive)), averaged
within each subreddit first, then across subreddits.
Secondary measure: monthly mean price-direction score.
Claim "text sentiment leads inflation" only if ALL hold: (1) the correlation
at a positive lag exceeds the lag-zero value, with a smooth pattern across
neighboring lags; (2) it also holds on month-to-month changes; (3) it holds
in the 2024-onward holdout; (4) the reverse direction (CPI leading text) is
not larger. Otherwise report it as no reliable lead.