# Organic insights

```bash
python3 scripts/insights.py page --period day --since 2026-08-01 --until 2026-08-25
python3 scripts/insights.py post POST_ID
python3 scripts/insights.py instagram-account --period day --metric-type total_value --since 2026-08-01 --until 2026-08-25
python3 scripts/insights.py instagram-media MEDIA_ID
```

Override defaults with `--metrics metric_one,metric_two`. Available metrics vary by Page vs Instagram, media type, account eligibility, date, and Graph API version. A valid token can still receive an unsupported-metric error; remove only the rejected metric and preserve the others. Values can be estimated, delayed, unavailable for low-volume content, or subject to retention limits. State the query period and distinguish API values from calculated rates.
