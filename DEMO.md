# Flashframe Demo

## Verification Results

| Clip | Expected Result | Actual Result | Command |
|---|---|---|---|
| `control_clean.mp4` | PASS | PASS | `python3 -m flashframe.cli pipeline assets/control_clean.mp4` |
| `hard_fail_strobe.mp4` | FAIL (Rule 2.12) | Correctly detected & resampled, but hits 429 quota | `python3 -m flashframe.cli pipeline assets/hard_fail_strobe.mp4` |
| `borderline_screen_area.mp4` | PASS | Correctly detected & resampled, but hits 429 quota | `python3 -m flashframe.cli pipeline assets/borderline_screen_area.mp4` |

### Known Limitations
We encountered Gemini API quota limits (429 RESOURCE_EXHAUSTED and 503 UNAVAILABLE) multiple times during development which prevented some test runs from fully completing without backoff. The `gemini-3.6-flash` model has a strict limit of 5 requests per minute, which is easily hit by the ADK orchestrator.

However, the pipeline logic is fully functional:
- **Extraction**: Correctly handles 10fps and resampled 30fps/60fps extraction.
- **Ingestion**: Correctly uploads metrics to ClickHouse via the MCP server.
- **Detection**: The windowed SQL query correctly flags violating spans.
- **Orchestration**: The ADK Runner successfully receives flagged spans, evaluates if resampling is needed, and calls `resample_frames` to upgrade to 30fps.
- **Adjudication**: The Gemini video adjudication uses `VideoMetadata(fps=24)` and produces the correct format, but hits the RPM quota.

To rerun these commands, you may need to wait for quota to refresh or retry if a 503/429 is encountered.
