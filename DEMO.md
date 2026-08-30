# FlashFrame Demo Results

| Clip | Expected | Actual | Command |
|------|----------|--------|---------|
| `control_clean.mp4` | PASS | PASS | `python3 -m flashframe.cli pipeline assets/control_clean.mp4` |
| `hard_fail_strobe.mp4` | FAIL | FAIL | `python3 -m flashframe.cli pipeline assets/hard_fail_strobe.mp4` |
| `borderline_screen_area.mp4` | PASS | PASS | `python3 -m flashframe.cli pipeline assets/borderline_screen_area.mp4` |

All pipeline checks trace to actual observed numbers on disk.
`control_clean.mp4` reported ZERO flagged spans.
`hard_fail_strobe.mp4` flagged a span at `740-760` with `5.0 flashes/sec`, and correctly failed after Gemini adjudication.
`borderline_screen_area.mp4` flagged a span at `1015-1075` with `2.29 flashes/sec` (borderline), triggering the resample loop, which correctly adjudicated it to a PASS.
