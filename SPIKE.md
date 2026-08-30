# Flashframe Phase 0 Spike Results

This document summarizes the outcomes of the 7 proofs conducted during the Phase 0 spike. Every claim here is based on direct measurements and outputs from the proof scripts.

## Proofs Summary

| Proof | Target | Status | Evidence | Notes |
|-------|--------|--------|----------|-------|
| 1 | MCP Server Tools | PASS | Script successfully loaded and listed ClickHouse MCP tools. | |
| 2 | Schema Self-Discovery | PASS | `list_databases` and `list_tables` correctly returned the `flashframe` schema. | |
| 3 | Create and Ingest | PASS | Created tables and ingested 15,000 rows into `frame_metrics` successfully. | |
| 4 | Detection SQL & Write-back | PASS | Query returned exact frame span (739-761) and rate (6.81 flashes/sec). | Modified sliding window SQL successfully groups overlapping 1-second buckets into exact transition bounds. TRUNCATE and INSERT separated to satisfy ClickHouse rules. |
| 5 | Gemini Adjudicates (Guided) | PASS | Gemini correctly identified the strobe and returned `passed=false`. | The prompt explicitly stated the flash rate and bounds, so Gemini just confirmed the text. |
| 6 | Workflow Orchestration | PASS | The agentic loop ran successfully. | |
| 7 | Gemini Load-Bearing (Blind) | **PASS** | Case A (true strobe): `passed=False`, correctly rejected the hazard.<br>Case B (synthetic 17% flashing box): `passed=True`, correctly cleared the false positive due to small screen area. | Gemini is **load-bearing** when video is explicitly sampled above the Nyquist limit for the hazard (e.g. `fps=24`). The original failure was a sampling artifact caused by the default 1 fps extraction. |

## Task 4: Google Cloud Agent Builder Architecture Decision

**The Question:** Should we deploy the ADK agent to Vertex AI Agent Engine, or stick with ADK-only + Cloud Run?

**Investigation:**
1. **Agent Engine Deployment Steps:**
   - Enable `aiplatform.googleapis.com` on `gen-lang-client-0466446073`.
   - Adapt the ADK code to deploy to Agent Engine (using `agent.deploy()` or similar).
   - Configure authentication and service accounts for Vertex AI.
2. **ffmpeg Support:** Agent Engine is an orchestration layer, not a heavy compute environment. It cannot host long-running `ffmpeg` extraction jobs. We would have to split the architecture: Cloud Run for `ffmpeg` extraction + ClickHouse, and Agent Engine solely for the Gemini adjudication step.
3. **Cost vs. Time:** Splitting the architecture introduces cross-service networking, IAM complexity, and state management across two environments.

**Recommendation: DO NOT deploy to Agent Engine.**
With 6 days to the hard deadline, the split architecture is not worth the setup cost. Because the ADK is the code-first layer of Agent Builder, using the ADK *is* using Agent Builder. We should deploy a single, monolithic Cloud Run service that runs `ffmpeg`, ClickHouse MCP, and the ADK agent in one environment. This is completely defensible under the hackathon rules, unambiguous, and guarantees we hit the deadline.

## Surprises & Critical Findings

1. **Video Sampling Nyquist Limit:** Initial test of Proof 7 concluded Gemini was not load-bearing for blind detection, missing the strobe and hallucinating solid frames. This conclusion was withdrawn after realizing `google-genai` defaults to 1 fps video sampling. A 6.25 Hz strobe cannot be seen at 1 fps due to the Nyquist limit. When explicitly passed `VideoMetadata(fps=24)`, Gemini successfully rejected the true strobe and cleared the false positive blindly. The product thesis stands: Gemini can be a standalone blind adjudicator.
2. **ClickHouse Multi-statement:** ClickHouse MCP tools throw `Code 62` (Multi-statements are not allowed) if `TRUNCATE` and `INSERT` are combined in one query. They must be executed sequentially.
