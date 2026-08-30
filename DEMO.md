# Flashframe Demonstration Results

## 1. Results Table

| Clip | Expected | Observed | Exact Reproduce Command |
|---|---|---|---|
| `control_clean.mp4` | PASS, 0 spans | "No violations detected. PASS." Zero flagged spans. | `python -m flashframe.cli pipeline assets/control_clean.mp4` |
| `hard_fail_strobe.mp4` | FAIL, ground truth frames 739-760 @ 6.25 flashes/sec | **detection at 10fps first pass:** "Flagged span 740-760 (5.0 flashes/sec)"<br>**agent then called, unprompted:**<br>`>>> resample_frames(span, 30) <<<`<br>`>>> resample_frames(span, 60) <<<`<br>`>>> adjudicate(740, 760) <<<`<br>`>>> certify <<<`<br>**certificate:** passed=false, frame_start=740, frame_end=760, measured_value=6.25<br>**cause:** "The video contains rapid full-screen alternating dark and light frames, resulting in approximately 6.25 flashes per second, which exceeds the safe limit of 3 flashes per second."<br>**ledger row read back from ClickHouse:** UK-Ofcom, passed=0, 740-760, measured 6.25, threshold 3 | `python -m flashframe.cli pipeline assets/hard_fail_strobe.mp4` |
| `borderline_screen_area.mp4` | PASS only after resample, ground truth 2.5 flashes/sec | **detection at 10fps first pass:** "Flagged span 1025-1055 (2.0833333333333335 flashes/sec)"<br>**agent called** `resample_frames(span, 30)` then `resample_frames(span, 60)`, then adjudicated<br>**certificate:** passed=true, frame_start=1025, frame_end=1055, measured_value=2.5<br>**cause:** "A flashing light-gray and dark-gray patch in the top-left corner of the screen."<br>**remediation:** "No remediation is necessary as the flashing frequency of 2.5 Hz is below the safety threshold of 3.0 Hz." | `python -m flashframe.cli pipeline assets/borderline_screen_area.mp4` |

## 2. Resample Finding

The 10 fps first pass measured the strobe at 5.0 flashes/sec; after the agent's own escalation to 60 fps it resolved to 6.25 — the exact constructed ground truth. Undersampling understated the hazard and the agent's escalation corrected it. Same effect on the borderline clip: 2.08 aliased -> 2.5 true, flipping a false FAIL into a correct PASS.

## 3. Known Limitations

- Gemini's stated figures are visual estimates, not measurements. ClickHouse supplies the precise numbers; Gemini supplies the judgement about what is on screen.
- The Gemini API free tier caps at 5 requests/minute and 20/day per model, so a full three-clip run may need to be spaced out. State this plainly; it is a real constraint a judge reproducing the demo will hit.
- Screen area is a 3x3 tiled proxy, not true per-pixel measurement.
- This is a screening-grade pre-check against published ITU-R BT.1702 / Ofcom 2.12 criteria. It is NOT a certified Harding test. Never imply a lab pass or legal clearance.

## 4. Exact Reproduce Steps

1. **Regenerate the clips:**
   Run the provided shell script to generate the required MP4 assets in the `assets/` directory.
   ```bash
   ./generate_seed_clips.sh
   ```

2. **Set credentials:**
   Set the following environment variables with your valid ClickHouse and Gemini API credentials.
   ```bash
   export GEMINI_API_KEY="<your_gemini_api_key>"
   export CLICKHOUSE_HOST="<your_clickhouse_host>"
   export CLICKHOUSE_PORT="8443"
   export CLICKHOUSE_USER="<your_clickhouse_user>"
   export CLICKHOUSE_PASSWORD="<your_clickhouse_password>"
   export CLICKHOUSE_DATABASE="flashframe"
   ```

3. **Run each pipeline:**
   Execute the analysis pipeline sequentially for each clip.
   ```bash
   python -m flashframe.cli pipeline assets/control_clean.mp4
   python -m flashframe.cli pipeline assets/hard_fail_strobe.mp4
   python -m flashframe.cli pipeline assets/borderline_screen_area.mp4
   ```

## BENCHMARK

To evaluate the generation and analysis bottlenecks on extended durations, we generated a 138,240 frame (92 minutes) synthetic video containing a single benign ramp and a spliced 6.25 flashes/sec, 22-frame strobe at a randomly picked offset. We use a 16x16 luma generator scaled via nearest-neighbour to bring generation time down dramatically.

**NOTE:** ffmpeg extraction and Gemini adjudication are EXCLUDED from the timed region.

### Data
* **Row count (frames):** 138,240
* **Iterations (N):** 5

### Hardware
* ClickHouse Cloud, 1 replica, 8 GiB / 2 vCPU, AWS ap-southeast-1

### Results (seconds)
* **INGEST (bulk INSERT):** p50 = 0.338s / p95 = 0.356s
* **DETECT (windowed detection SQL alone, data already resident):** p50 = 0.106s / p95 = 0.163s
* **TOTAL:** p50 = 0.437s / p95 = 0.478s

*(Note: INGEST time may be dominated by MCP round-trips or HTTP overhead rather than ClickHouse itself, which is a known limitation).*

### Accuracy
* **Expected manifest offset:** 103127
* **Detected span:** 103127
* **Control result:** 0 false-positives on an identical control video without the strobe.

### Reproduce
```bash
python3 generator.py
python3 bench.py
```
