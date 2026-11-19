# Gemini 429 bodies

**`429_embed_per_day.json` is a real capture** — the body the API returned on 2026-11-19 when a
re-ingest hit the daily embedding limit (`retryDelay` ~74000s, i.e. "retry in 20h34m"). It replaced a
version reconstructed from the 2026-09-14 log, which had said `57s`: the API's delay on a daily limit
is not even consistent with itself, which is the case for reading `quotaId` instead.

**`429_embed_per_minute.json` and `429_generate_per_day.json` are reconstructed**, following the
same shape with quota ids named the same way. Replace either with a real capture when one is seen;
the tests depend only on `details[].violations[].quotaId`.
