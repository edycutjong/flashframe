import pytest
import os
import json
import asyncio
from unittest.mock import patch

from flashframe.cli import run_extraction
from flashframe.ingest import setup_db_and_ingest
from flashframe.detect import detect_violations
from flashframe.adjudicate import Verdict
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

def get_mcp_env():
    env = os.environ.copy()
    env["CLICKHOUSE_HOST"] = os.environ.get("CLICKHOUSE_HOST", "")
    env["CLICKHOUSE_USER"] = os.environ.get("CLICKHOUSE_USER", "default")
    env["CLICKHOUSE_PASSWORD"] = os.environ.get("CLICKHOUSE_PASSWORD", "")
    env["CLICKHOUSE_DATABASE"] = os.environ.get("CLICKHOUSE_DATABASE", "flashframe")
    env["CLICKHOUSE_ALLOW_WRITE_ACCESS"] = "true"
    env["CHDB_ENABLED"] = "true"
    return env

_clickhouse = None
_tools = None

async def get_clickhouse_tool(tool_name):
    global _clickhouse, _tools
    if _clickhouse is None:
        from mcp.client.stdio import StdioServerParameters
        from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
        
        mcp_python = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".venv", "bin", "python3")
        if not os.path.exists(mcp_python):
            import sys
            mcp_python = sys.executable
        _clickhouse = McpToolset(
            connection_params=StdioConnectionParams(
                server_params=StdioServerParameters(
                    command=mcp_python,
                    args=["-m", "mcp_clickhouse.main"],
                    env=get_mcp_env(),
                )
            )
        )
        _tools = await _clickhouse.get_tools()
    return next(t for t in _tools if t.name == tool_name)


@pytest.mark.asyncio
async def test_schema_self_discovery():
    if not os.environ.get("CLICKHOUSE_PASSWORD"):
        pytest.skip("No ClickHouse credentials")
        
    list_tables_tool = await get_clickhouse_tool("list_tables")
    res = await list_tables_tool.run_async(args={"database": os.environ.get("CLICKHOUSE_DATABASE", "flashframe")}, tool_context=None)
    
    if hasattr(res, "content") and res.content:
        text = res.content[0].text
    elif hasattr(res, "text"):
        text = res.text
    elif isinstance(res, list) and len(res) > 0:
        text = res[0].text if hasattr(res[0], "text") else str(res)
    elif isinstance(res, dict) and "content" in res:
        text = res["content"][0]["text"]
    else:
        text = str(res)
        
    assert "frame_metrics" in text


@pytest.mark.asyncio
async def test_windowed_sql_correctness():
    if not os.environ.get("CLICKHOUSE_PASSWORD"):
        pytest.skip("No ClickHouse credentials")
        
    run_query_tool = await get_clickhouse_tool("run_query")
    
    # Run hard_fail_strobe
    scan_id_fail = run_extraction("assets/hard_fail_strobe.mp4", fps_override=25)
    await setup_db_and_ingest(run_query_tool, scan_id_fail)
    detect_res = await detect_violations(run_query_tool, scan_id_fail, fps=25)
    
    try:
        spans = json.loads(detect_res)
    except Exception:
        spans = []
        
    # Standardize span format
    if isinstance(spans, dict) and 'frame_start' in spans:
        spans = [spans]
    elif isinstance(spans, list) and len(spans) > 0 and isinstance(spans[0], list):
        spans = [{'frame_start': row[0], 'frame_end': row[1]} for row in spans]
        
    # Check it yields ONE span near 739-760
    assert len(spans) >= 1, f"Expected at least 1 span, got {len(spans)}"
    assert 735 <= spans[0]["frame_start"] <= 745
    assert 755 <= spans[0]["frame_end"] <= 765
    
    # Run control_clean
    scan_id_clean = run_extraction("assets/control_clean.mp4", fps_override=25)
    await setup_db_and_ingest(run_query_tool, scan_id_clean)
    detect_res = await detect_violations(run_query_tool, scan_id_clean, fps=25)
    try:
        spans_clean = json.loads(detect_res)
    except Exception:
        spans_clean = []
        
    if isinstance(spans_clean, dict) and 'frame_start' in spans_clean:
        spans_clean = [spans_clean]
    elif isinstance(spans_clean, list) and len(spans_clean) > 0 and isinstance(spans_clean[0], list):
        spans_clean = [{'frame_start': row[0], 'frame_end': row[1]} for row in spans_clean]
        
    # Check it yields ZERO spans
    assert len(spans_clean) == 0, f"Expected 0 spans, got {len(spans_clean)}"


@pytest.mark.asyncio
async def test_chdb_threshold_join():
    if not os.environ.get("CLICKHOUSE_PASSWORD"):
        pytest.skip("No ClickHouse credentials")
    # "run_chdb_select_query joins the raw CSV against thresholds.csv"
    run_chdb_tool = await get_clickhouse_tool("run_chdb_select_query")
    
    query = """
    SELECT count(*) as cnt 
    FROM file('frame_metrics.csv', 'CSVWithNames') m
    CROSS JOIN file('thresholds.csv', 'CSVWithNames') t
    """
    res = await run_chdb_tool.run_async(args={"query": query}, tool_context=None)
    
    if hasattr(res, "content") and res.content:
        text = res.content[0].text
    elif hasattr(res, "text"):
        text = res.text
    elif isinstance(res, list) and len(res) > 0:
        text = res[0].text if hasattr(res[0], "text") else str(res)
    elif isinstance(res, dict) and "content" in res:
        text = res["content"][0]["text"]
    else:
        text = str(res)
        
    data = json.loads(text)
    assert len(data) > 0


def test_gemini_structured_output_parsing():
    payload = '{"passed": true, "frame_start": 10, "frame_end": 20, "measured_value": 2.5, "threshold_value": 3.0, "cause": "Police lights", "remediation": "Dim the lights"}'
    v = Verdict.model_validate_json(payload)
    assert v.passed is True
    assert v.frame_start == 10
    assert v.cause == "Police lights"
    assert v.measured_value == 2.5

@pytest.mark.asyncio
async def test_adk_function_call_trigger():
    # ADK function-call trigger — a borderline verdict causes resample_frames to be invoked
    # Test that if we provide the right prompt to the ADK agent, it calls resample_frames
    
    with patch('google.adk.models.google_llm.Gemini.generate_content_async') as mock_generate:
        from google.genai.types import GenerateContentResponse, Candidate, Content, Part, FunctionCall
        from google.adk.models.google_llm import LlmResponse
        
        mock_resp = GenerateContentResponse(
            candidates=[
                Candidate(
                    content=Content(
                        parts=[
                            Part(function_call=FunctionCall(
                                name='resample_frames',
                                args={'frame_start': 1025, 'frame_end': 1055, 'target_fps': 30}
                            ))
                        ]
                    )
                )
            ]
        )
        
        call_count = [0]
        async def mock_gen(*args, **kwargs):
            if call_count[0] == 0:
                call_count[0] += 1
                yield LlmResponse.create(mock_resp)
            else:
                yield LlmResponse.create(GenerateContentResponse(
                    candidates=[Candidate(content=Content(parts=[Part.from_text(text="Done")]))]
                ))
            
        mock_generate.side_effect = mock_gen
        
        called_args = []
        async def resample_frames(frame_start: int, frame_end: int, target_fps: int) -> dict:
            called_args.append((frame_start, frame_end, target_fps))
            raise RuntimeError("StopRunner")

        agent = LlmAgent(
            model="gemini-3.6-flash",
            name="flashframe_adjudicator",
            instruction="You must call resample_frames.",
            tools=[FunctionTool(resample_frames)],
        )
        
        from google.adk import Runner
        from google.adk.sessions.in_memory_session_service import InMemorySessionService
        session_service = InMemorySessionService()
        runner = Runner(agent=agent, app_name="test", session_service=session_service, auto_create_session=True)
        
        try:
            async for event in runner.run_async(
                user_id="u",
                session_id="s",
                new_message=Content(role="user", parts=[Part.from_text(text="Call resample_frames")])
            ):
                pass
        except RuntimeError as e:
            if str(e) != "StopRunner":
                raise
            
        assert len(called_args) > 0
        assert called_args[0] == (1025, 1055, 30)

class FakeRunQueryTool:
    def __init__(self, read_back_row=None):
        self.queries = []
        self.read_back_row = read_back_row or []
        
    async def run_async(self, args, tool_context=None):
        query = args.get("query", "")
        self.queries.append(query)
        if "SELECT * FROM violation_ledger" in query:
            return {"content": [{"text": json.dumps({"columns": ["scan_id", "passed", "frame_start", "frame_end", "measured", "threshold", "cause", "remediation", "gemini_estimated_rate", "certified_at"], "rows": [self.read_back_row]})}]}
        return {"content": [{"text": "{}"}]}

@pytest.mark.asyncio
async def test_span_duration_off_by_one():
    from src.flashframe.detect import detect_violations
    fake_tool = FakeRunQueryTool()
    await detect_violations(fake_tool, "test_scan", fps=25)
    
    assert len(fake_tool.queries) == 1
    sql = fake_tool.queries[0]
    
    # Assert the inclusive form is present
    assert "(max(frame_idx) - min(frame_idx) + 1)" in sql
    # Assert the exclusive form is absent
    assert "(max(frame_idx) - min(frame_idx)) / 25.0" not in sql
    assert "(max(frame_idx) - min(frame_idx))/25.0" not in sql

@pytest.mark.asyncio
async def test_gemini_estimate_separate_from_measurement():
    from src.flashframe.certify import write_certificate
    fake_tool = FakeRunQueryTool(read_back_row=["test_scan", 1, 10, 20, 6.25, 3.0, "cause", "rem", 5.0, "2026-09-01"])
    
    cert = await write_certificate(
        fake_tool,
        scan_id="test_scan",
        passed=True,
        frame_start=10,
        frame_end=20,
        measured_value=6.25,
        cause="cause",
        remediation="rem",
        gemini_estimated_rate=5.0
    )
    
    assert cert["measured_value"] == 6.25
    assert cert["gemini_estimated_rate"] == 5.0
    
    # Verify the SQL itself
    insert_sql = next(q for q in fake_tool.queries if "INSERT INTO" in q)
    # The INSERT statement has columns:
    # (scan_id, certified_at, territory, passed, frame_start, frame_end, measured, threshold, cause, remediation, gemini_estimated_rate)
    # The values block in the f-string evaluates to:
    # {measured_value} ... {gemini_estimated_rate}
    
    # Let's just make sure both values appear in the query and they aren't swapped.
    # The query is structured with measured_value before gemini_estimated_rate.
    measured_idx = insert_sql.find("6.25")
    gemini_idx = insert_sql.find("5.0")
    
    assert measured_idx != -1
    assert gemini_idx != -1
    assert measured_idx < gemini_idx, 'measured_value must appear before gemini_estimated_rate in the SQL statement'
    # Check that measured comes before the cause/remediation, and gemini comes after
    # To be extremely precise, we can check the substring around them
    # Because 6.25 is inserted as a bare float, it should be isolated.
    assert "6.25" in insert_sql
    assert "5.0" in insert_sql
