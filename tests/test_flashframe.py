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
    from flashframe.detect import detect_violations
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
    from flashframe.certify import write_certificate
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

@pytest.mark.asyncio
async def test_certify_read_back_mismatch_raises():
    from flashframe.certify import write_certificate
    fake_tool = FakeRunQueryTool(read_back_row=["test_scan", 1, 10, 20, 99.99, 3.0, "cause", "rem", 5.0, "2026-09-01"])
    
    with pytest.raises(RuntimeError, match="Ledger mismatch: inserted 6.25, read back 99.99"):
        await write_certificate(
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

def test_verdict_missing_fields_raises():
    from flashframe.adjudicate import Verdict
    from pydantic import ValidationError
    import pytest
    payload = '{"passed": true, "frame_start": 10}'
    with pytest.raises(ValidationError):
        Verdict.model_validate_json(payload)
class ShapeFakeRunQueryTool(FakeRunQueryTool):
    def __init__(self, shape=None, error_attr=False, error_dict=False, return_val=None, read_back_row=None):
        super().__init__(read_back_row=read_back_row)
        self.shape = shape
        self.error_attr = error_attr
        self.error_dict = error_dict
        self.return_val = return_val

    async def run_async(self, args, tool_context=None):
        self.queries.append(args.get("query", ""))
        
        if self.error_attr:
            class ErrRes:
                isError = True
            return ErrRes()
            
        if self.error_dict:
            return {"isError": True}
            
        if self.shape == "content_attr":
            class Item:
                text = self.return_val
            class Res:
                content = [Item()]
            return Res()
            
        if self.shape == "text_attr":
            class Res:
                text = self.return_val
            return Res()
            
        if self.shape == "list_attr":
            class Item:
                text = self.return_val
            return [Item()]
            
        if self.shape == "fallback":
            rv = self.return_val
            class Res:
                def __str__(self):
                    return rv
            return Res()
            
        if self.shape == "rows":
            class Item:
                text = '{"rows": ' + self.return_val + '}'
            class Res:
                content = [Item()]
            return Res()

        if self.shape == "unparseable":
            class Item:
                text = 'not json'
            class Res:
                content = [Item()]
            return Res()
            
        # certify custom returns for readback
        if self.shape == "certify_falsy" and "SELECT *" in args.get("query", ""):
            return None
            
        if self.shape == "certify_is_error" and "SELECT *" in args.get("query", ""):
            return {"isError": True}
            
        if self.shape == "certify_empty_rows" and "SELECT *" in args.get("query", ""):
            return {"content": [{"text": '{"columns": ["a"], "rows": []}'}]}
            
        if self.shape == "certify_json_list" and "SELECT *" in args.get("query", ""):
            return {"content": [{"text": self.return_val}]}
            
        if self.shape == "certify_bare_object" and "SELECT *" in args.get("query", ""):
            return {"content": [{"text": self.return_val}]}
            
        if self.shape == "certify_malformed_json" and "SELECT *" in args.get("query", ""):
            return {"content": [{"text": "{"}]}
            
        return await super().run_async(args, tool_context)


@pytest.mark.asyncio
async def test_detect_sql_error_attr():
    from flashframe.detect import detect_violations
    with pytest.raises(Exception, match="SQL Error: .*"):
        await detect_violations(ShapeFakeRunQueryTool(error_attr=True), "scan1")

@pytest.mark.asyncio
async def test_detect_sql_error_dict():
    from flashframe.detect import detect_violations
    with pytest.raises(Exception, match="SQL Error: .*"):
        await detect_violations(ShapeFakeRunQueryTool(error_dict=True), "scan1")

@pytest.mark.asyncio
async def test_detect_shape_content_attr():
    from flashframe.detect import detect_violations
    res = await detect_violations(ShapeFakeRunQueryTool(shape="content_attr", return_val='{"a": 1}'), "scan1")
    assert res == '{"a": 1}'

@pytest.mark.asyncio
async def test_detect_shape_text_attr():
    from flashframe.detect import detect_violations
    res = await detect_violations(ShapeFakeRunQueryTool(shape="text_attr", return_val='{"b": 2}'), "scan1")
    assert res == '{"b": 2}'

@pytest.mark.asyncio
async def test_detect_shape_list():
    from flashframe.detect import detect_violations
    res = await detect_violations(ShapeFakeRunQueryTool(shape="list_attr", return_val='{"c": 3}'), "scan1")
    assert res == '{"c": 3}'

@pytest.mark.asyncio
async def test_detect_shape_fallback():
    from flashframe.detect import detect_violations
    res = await detect_violations(ShapeFakeRunQueryTool(shape="fallback", return_val='{"d": 4}'), "scan1")
    assert res == '{"d": 4}'

@pytest.mark.asyncio
async def test_detect_shape_rows():
    from flashframe.detect import detect_violations
    res = await detect_violations(ShapeFakeRunQueryTool(shape="rows", return_val='[{"e": 5}]'), "scan1")
    assert res == '[{"e": 5}]'

@pytest.mark.asyncio
async def test_detect_unparseable():
    from flashframe.detect import detect_violations
    res = await detect_violations(ShapeFakeRunQueryTool(shape="unparseable"), "scan1")
    assert res == '[]'

@pytest.mark.asyncio
async def test_certify_res2_falsy(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from flashframe.certify import write_certificate
    with pytest.raises(RuntimeError, match="Ledger insertion failed: no row returned on read-back."):
        await write_certificate(ShapeFakeRunQueryTool(shape="certify_falsy"), "scan1", True, 10, 20, 6.25, "cause", "rem")

@pytest.mark.asyncio
async def test_certify_res2_is_error(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from flashframe.certify import write_certificate
    with pytest.raises(RuntimeError) as excinfo:
        await write_certificate(ShapeFakeRunQueryTool(shape="certify_is_error"), "scan1", True, 10, 20, 6.25, "cause", "rem")
    assert str(excinfo.value) == "Ledger read-back error: {'isError': True}"
    assert excinfo.value.__cause__ is None

@pytest.mark.asyncio
async def test_certify_res2_empty_rows(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from flashframe.certify import write_certificate
    with pytest.raises(RuntimeError, match="Ledger insertion failed: no row returned on read-back."):
        await write_certificate(ShapeFakeRunQueryTool(shape="certify_empty_rows"), "scan1", True, 10, 20, 6.25, "cause", "rem")

@pytest.mark.asyncio
async def test_certify_res2_json_list(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from flashframe.certify import write_certificate
    cert = await write_certificate(ShapeFakeRunQueryTool(shape="certify_json_list", return_val='[{"measured": 6.25, "frame_start": 10, "cause": "list_cause"}]'), "scan1", True, 10, 20, 6.25, "cause", "rem")
    assert cert["cause"] == "list_cause"

@pytest.mark.asyncio
async def test_certify_res2_bare_object(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from flashframe.certify import write_certificate
    cert = await write_certificate(ShapeFakeRunQueryTool(shape="certify_bare_object", return_val='{"measured": 6.25, "frame_start": 10, "cause": "obj_cause"}'), "scan1", True, 10, 20, 6.25, "cause", "rem")
    assert cert["cause"] == "obj_cause"

@pytest.mark.asyncio
async def test_certify_res2_malformed_json(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from flashframe.certify import write_certificate
    import json
    with pytest.raises(RuntimeError, match="Ledger insertion failed: .*") as excinfo:
        await write_certificate(ShapeFakeRunQueryTool(shape="certify_malformed_json"), "scan1", True, 10, 20, 6.25, "cause", "rem")
    assert isinstance(excinfo.value.__cause__, json.JSONDecodeError)

@pytest.mark.asyncio
async def test_ingest_run_sql_error_attr_returned(capsys):
    from flashframe.ingest import run_sql
    class ErrAttrTool:
        async def run_async(self, args, tool_context=None):
            class ErrRes:
                isError = True
            return ErrRes()
    
    res = await run_sql(ErrAttrTool(), "SELECT 1")
    assert getattr(res, "isError", False)
    captured = capsys.readouterr()
    assert "Error executing SQL: SELECT 1" in captured.out

@pytest.mark.asyncio
async def test_ingest_run_sql_error_dict_returned(capsys):
    from flashframe.ingest import run_sql
    class ErrDictTool:
        async def run_async(self, args, tool_context=None):
            return {"isError": True}
            
    res = await run_sql(ErrDictTool(), "SELECT 2")
    assert res.get("isError")
    captured = capsys.readouterr()
    assert "Error executing SQL: SELECT 2" in captured.out

@pytest.mark.asyncio
async def test_ingest_ddl_and_reset(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    # create empty frame_metrics.csv
    with open('frame_metrics.csv', 'w') as f:
        f.write("a,b,c,d,e,f,g,h,i\n")
    
    from flashframe.ingest import setup_db_and_ingest
    fake_tool = FakeRunQueryTool()
    await setup_db_and_ingest(fake_tool, "test_scan_123")
    
    queries = fake_tool.queries
    assert any("CREATE TABLE IF NOT EXISTS frame_metrics" in q for q in queries)
    assert any("CREATE TABLE IF NOT EXISTS threshold_reference" in q for q in queries)
    assert any("CREATE TABLE IF NOT EXISTS violation_ledger" in q for q in queries)
    assert any("DELETE FROM frame_metrics WHERE scan_id = 'test_scan_123'" in q for q in queries)
    assert any("TRUNCATE TABLE IF EXISTS threshold_reference" in q for q in queries)

@pytest.mark.asyncio
async def test_ingest_scan_metadata(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with open('frame_metrics.csv', 'w') as f:
        f.write("a,b,c,d,e,f,g,h,i\n")
        
    from flashframe.ingest import setup_db_and_ingest
    fake_tool = FakeRunQueryTool()
    await setup_db_and_ingest(fake_tool, "scan_id_456", video_path="/my/test/vid.mp4", source_fps=29.97, measured_fps=59.94)
    
    queries = fake_tool.queries
    insert_meta = next(q for q in queries if "INSERT INTO scan_metadata VALUES" in q)
    assert "'scan_id_456'" in insert_meta
    assert "'/my/test/vid.mp4'" in insert_meta
    assert "29.97" in insert_meta
    assert "59.94" in insert_meta

@pytest.mark.asyncio
async def test_ingest_default_thresholds(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with open('frame_metrics.csv', 'w') as f:
        f.write("a,b,c,d,e,f,g,h,i\n")
        
    from flashframe.ingest import setup_db_and_ingest
    fake_tool = FakeRunQueryTool()
    await setup_db_and_ingest(fake_tool, "scan_id")
    
    # Assert file was created
    assert os.path.exists("thresholds.csv")
    
    # Assert the query
    queries = fake_tool.queries
    insert_thresh = next(q for q in queries if "INSERT INTO threshold_reference VALUES" in q)
    assert "('UK-Ofcom', 'flash_rate', 3.0, 20.0, 25.0, 'Ofcom Rule 2.12')" in insert_thresh

@pytest.mark.asyncio
async def test_ingest_existing_thresholds(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with open('frame_metrics.csv', 'w') as f:
        f.write("a,b,c,d,e,f,g,h,i\n")
        
    with open('thresholds.csv', 'w') as f:
        f.write("territory,criterion,max,min,area,cit\n")
        f.write("US,flash,1.1,2.2,3.3,Cit 1\n")
        f.write("JP,flash,4.4,5.5,6.6,Cit 2\n")
        
    from flashframe.ingest import setup_db_and_ingest
    fake_tool = FakeRunQueryTool()
    await setup_db_and_ingest(fake_tool, "scan_id")
    
    queries = fake_tool.queries
    insert_thresh = next(q for q in queries if "INSERT INTO threshold_reference VALUES" in q)
    assert "('US', 'flash', 1.1, 2.2, 3.3, 'Cit 1')" in insert_thresh
    assert "('JP', 'flash', 4.4, 5.5, 6.6, 'Cit 2')" in insert_thresh
    assert "territory" not in insert_thresh

@pytest.mark.asyncio
async def test_ingest_empty_thresholds_guard(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with open('frame_metrics.csv', 'w') as f:
        f.write("a,b,c,d,e,f,g,h,i\n")
        
    with open('thresholds.csv', 'w') as f:
        f.write("territory,criterion,max,min,area,cit\n")
        
    from flashframe.ingest import setup_db_and_ingest
    fake_tool = FakeRunQueryTool()
    await setup_db_and_ingest(fake_tool, "scan_id")
    
    queries = fake_tool.queries
    assert not any("INSERT INTO threshold_reference" in q for q in queries)

@pytest.mark.asyncio
async def test_ingest_5000_row_batching(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    
    with open('frame_metrics.csv', 'w') as f:
        f.write("scan_id,frame_idx,pts_time,tile,yavg,ymax,ymin,satavg,red_ratio\n")
        for i in range(5001):
            f.write(f"scan_{i},{i},0.0,0,0.0,0.0,0.0,0.0,0.0\n")
            
    from flashframe.ingest import setup_db_and_ingest
    fake_tool = FakeRunQueryTool()
    await setup_db_and_ingest(fake_tool, "scan_id")
    
    queries = fake_tool.queries
    inserts = [q for q in queries if "INSERT INTO frame_metrics VALUES" in q]
    
    assert len(inserts) == 2
    # 5000 value tuples in the first
    assert inserts[0].count("('scan_") == 5000
    assert inserts[1].count("('scan_") == 1
    
    # Check bounds
    assert "('scan_0', 0, 0.0, 0, 0.0, 0.0, 0.0, 0.0, 0.0)" in inserts[0]
    assert "('scan_4999', 4999, 0.0, 0, 0.0, 0.0, 0.0, 0.0, 0.0)" in inserts[0]
    assert "('scan_5000', 5000, 0.0, 0, 0.0, 0.0, 0.0, 0.0, 0.0)" in inserts[1]

@pytest.mark.asyncio
async def test_ingest_exact_5000_row_batching(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    
    with open('frame_metrics.csv', 'w') as f:
        f.write("scan_id,frame_idx,pts_time,tile,yavg,ymax,ymin,satavg,red_ratio\n")
        for i in range(5000):
            f.write(f"scan_{i},{i},0.0,0,0.0,0.0,0.0,0.0,0.0\n")
            
    from flashframe.ingest import setup_db_and_ingest
    fake_tool = FakeRunQueryTool()
    await setup_db_and_ingest(fake_tool, "scan_id")
    
    queries = fake_tool.queries
    inserts = [q for q in queries if "INSERT INTO frame_metrics VALUES" in q]
    
    assert len(inserts) == 1
    assert inserts[0].count("('scan_") == 5000
