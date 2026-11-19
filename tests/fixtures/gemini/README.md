# Gemini 429 bodies

**Reconstructed, not captured.** Each file follows the shape of a Gemini 429
(`google.rpc.QuotaFailure` + `RetryInfo`), with quota ids as logged:
`EmbedContentRequestsPerDayPerProjectPerModel-FreeTier` and its `57s` retry delay
come from the failed rebuild of 2026-09-14 (`engineering/bugs-log.md`). The
per-minute and generate ids follow the same naming. Replace any file with a real
capture when one is seen; the tests only depend on `details[].violations[].quotaId`.
