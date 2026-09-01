# Flashframe Judge Index

> Upload a locked cut, get a broadcast photosensitivity safety certificate — before you pay for the lab pass.

## The 30-Second Path

1. Open https://flashframe-production.up.railway.app
2. Click one of the three bundled seed clips (`control_clean`, `hard_fail_strobe`, `borderline_screen_area`) — no upload, no keys, no arguments, no flags
3. Watch the scan and read the issued certificate

*Honest warning: ClickHouse Cloud auto-suspends when idle, so a cold first query costs ~25 s; the app issues a warm-up query on startup and the UI says what it is doing. A judge who hits a 25 s wait without warning assumes it is broken.*

## The Receipt

*Note: All figures cross-checked exactly against README.md and DEMO.md.*

- Conditions: 2026-09-01, freshly generated 138,240-frame clip (92 min 9.6 s at 25 fps), `frame_metrics` truncated to a single scan beforehand, N=5 iterations per run, ClickHouse Cloud 1 replica / 8 GiB / 2 vCPU / AWS ap-southeast-1
- INGEST: p50 2.888 s / p95 8.843 s (warm) vs p50 2.486 s / p95 3.379 s (cold)
- DETECT (windowed SQL, data resident): p50 0.550 s / p95 0.940 s (warm) vs p50 0.423 s / p95 14.252 s (cold start spike)
- TOTAL: p50 3.548 s / p95 9.471 s (warm) vs p50 2.910 s / p95 16.521 s (cold)
- Accuracy: manifest offset 57896 → detected span 57896, on all 5 iterations of both runs — 10/10, zero mismatches; control feature 0 false positives
- `hard_fail_strobe.mp4`: ground truth FAIL frames 739-760 @ 6.25 flashes/sec → observed FAIL frames 740-760 @ 6.25, exact
- Test suite: 99 tests, 96 pass with no credentials at all, 100% statement coverage on 604 statements

## The Reproduce Command

```bash
git clone https://github.com/edycutjong/flashframe.git
cd flashframe
uv pip install -e . -r requirements.txt
# credentials from ~/.config
python -m flashframe.cli
```

### Credential-Free Test Suite
The product itself requires live ClickHouse + Gemini credentials. The test suite does not:
- 96 of 99 tests pass with no credentials at all on a fresh clone.

## Honest Limitations

- Gemini free tier 5 req/min, 20/day; the resample loop is multi-call so a demo run may need spacing; the app catches 429 and says so.
- ClickHouse Cloud cold start ~25 s.
- INGEST is round-trip bound, likely dominated by MCP/HTTP rather than ClickHouse — which is why the stages are reported separately rather than as one blended TOTAL.
- The public demo video title quotes an earlier 106 ms DETECT p50 which later measurement did not reproduce; the figures in this document are the current measured ones.

*Disclaimer: Flashframe is a screening-grade pre-check against published ITU-R BT.1702 / Ofcom 2.12 criteria. It is **not** a certified Harding test and does not imply a lab pass or legal clearance.*

## Links

- **Live App:** https://flashframe-production.up.railway.app
- **Repo:** https://github.com/edycutjong/flashframe
- **Demo Video:** https://youtu.be/rPxGyYpVfAE
