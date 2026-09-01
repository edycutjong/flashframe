# Flashframe Judge Index

> Upload a locked cut, get a broadcast photosensitivity safety certificate — before you pay for the lab pass.

## The 30-Second Path

1. Open https://flashframe-production.up.railway.app
2. Click one of the three bundled seed clips (`control_clean`, `hard_fail_strobe`, `borderline_screen_area`) — no upload, no keys, no arguments, no flags
3. Watch the scan and read the issued certificate

*Honest warning: ClickHouse Cloud auto-suspends when idle, so a cold first query costs ~25 s; the app issues a warm-up query on startup and the UI says what it is doing. A judge who hits a 25 s wait without warning assumes it is broken.*

## The Receipt

*Note: All figures cross-checked exactly against README.md and DEMO.md.*

- 138,240 frames (92 min 9.6 s at 25 fps), N=5, ClickHouse Cloud 1 replica / 8 GiB / 2 vCPU / AWS ap-southeast-1
- INGEST p50 0.338 s / p95 0.356 s
- DETECT (windowed SQL, data resident) p50 0.106 s / p95 0.163 s
- TOTAL p50 0.437 s / p95 0.478 s
- Accuracy: manifest offset 103127 → detected span 103127; control feature 0 false positives
- `hard_fail_strobe.mp4`: ground truth FAIL frames 739-760 @ 6.25 flashes/sec → observed FAIL frames 740-760 @ 6.25, exact
- Test suite: 101 tests, 98 pass with no credentials at all, 100% statement coverage on 621 statements

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
- 98 of 101 tests pass with no credentials at all on a fresh clone.

## Honest Limitations

- Gemini free tier 5 req/min, 20/day; the resample loop is multi-call so a demo run may need spacing; the app catches 429 and says so.
- ClickHouse Cloud cold start ~25 s.
- INGEST is round-trip bound, likely dominated by MCP/HTTP rather than ClickHouse — which is why the stages are reported separately rather than as one blended 0.437 s.

*Disclaimer: Flashframe is a screening-grade pre-check against published ITU-R BT.1702 / Ofcom 2.12 criteria. It is **not** a certified Harding test and does not imply a lab pass or legal clearance.*

## Links

- **Live App:** https://flashframe-production.up.railway.app
- **Repo:** https://github.com/edycutjong/flashframe
- **Demo Video:** https://youtu.be/rPxGyYpVfAE
