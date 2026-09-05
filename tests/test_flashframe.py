import pytest
import os
import json
from unittest.mock import patch, MagicMock

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

class MockFfmpegRunner:
    def __init__(self, stats_contents=None):
        self.argv = None
        self.kwargs = None
        self.stats_contents = stats_contents or {}
        
    def __call__(self, cmd, **kwargs):
        self.argv = cmd
        self.kwargs = kwargs
        for k, v in self.stats_contents.items():
            with open(f"stats_{k}.txt", "w") as f:
                f.write(v)

def test_extract_filtergraph_and_uuid(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    fake = MockFfmpegRunner()
    from flashframe.extract import run_extraction
    import uuid
    
    with patch("flashframe.extract.subprocess.run", fake):
        scan_id = run_extraction("vid.mp4", fps_override=10)
        
    assert isinstance(uuid.UUID(scan_id), uuid.UUID)
    
    with open("filter.txt") as f:
        content = f.read()
        
    assert content.startswith("fps=10,split=10")
    assert "[full]signalstats,metadata=print:file=stats_0.txt[out0]" in content
    assert "[t1]crop=iw/3:ih/3:0:0,signalstats,metadata=print:file=stats_1.txt[out1]" in content
    assert "[t2]crop=iw/3:ih/3:iw/3:0,signalstats,metadata=print:file=stats_2.txt[out2]" in content
    assert "[t3]crop=iw/3:ih/3:2*iw/3:0,signalstats,metadata=print:file=stats_3.txt[out3]" in content
    assert "[t4]crop=iw/3:ih/3:0:ih/3,signalstats,metadata=print:file=stats_4.txt[out4]" in content
    assert "[t5]crop=iw/3:ih/3:iw/3:ih/3,signalstats,metadata=print:file=stats_5.txt[out5]" in content
    assert "[t6]crop=iw/3:ih/3:2*iw/3:ih/3,signalstats,metadata=print:file=stats_6.txt[out6]" in content
    assert "[t7]crop=iw/3:ih/3:0:2*ih/3,signalstats,metadata=print:file=stats_7.txt[out7]" in content
    assert "[t8]crop=iw/3:ih/3:iw/3:2*ih/3,signalstats,metadata=print:file=stats_8.txt[out8]" in content
    assert "[t9]crop=iw/3:ih/3:2*iw/3:2*ih/3,signalstats,metadata=print:file=stats_9.txt[out9]" in content
    
    with patch("flashframe.extract.subprocess.run", fake):
        run_extraction("vid.mp4")
        
    with open("filter.txt") as f:
        content = f.read()
    assert content.startswith("split=10")
    assert "fps=" not in content

def test_extract_stale_stats_cleanup(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with open("stats_3.txt", "w") as f:
        f.write("junk data")
        
    fake = MockFfmpegRunner()
    from flashframe.extract import run_extraction
    with patch("flashframe.extract.subprocess.run", fake):
        run_extraction("vid.mp4")
        
    assert not os.path.exists("stats_3.txt")

def test_extract_ffmpeg_argv(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    fake = MockFfmpegRunner()
    from flashframe.extract import run_extraction
    with patch("flashframe.extract.subprocess.run", fake):
        run_extraction("my_video.mp4")
        
    assert "-filter_complex_script" in fake.argv
    assert "filter.txt" in fake.argv
    assert "-i" in fake.argv
    assert "my_video.mp4" in fake.argv
    assert "-f" in fake.argv
    assert "null" in fake.argv
    assert "-" in fake.argv
    for i in range(10):
        assert "-map" in fake.argv
        assert f"[out{i}]" in fake.argv
        
    assert fake.kwargs.get("check") is True

def test_extract_seek_arithmetic(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    fake = MockFfmpegRunner()
    from flashframe.extract import run_extraction
    with patch("flashframe.extract.subprocess.run", fake):
        run_extraction("vid.mp4", frame_start=100, frame_end=124)
        
    assert "-ss" in fake.argv
    assert "4.0" in fake.argv
    assert "-t" in fake.argv
    assert "1.0" in fake.argv
    
    with patch("flashframe.extract.subprocess.run", fake):
        run_extraction("vid.mp4", frame_start=None)
        
    assert "-ss" not in fake.argv
    assert "-t" not in fake.argv

def test_extract_parser_frame_index_formulas(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    # 0.2s is exactly 5 frames at 25fps.
    stats_content = (
        "frame:0    pts:0       pts_time:0.2\n"
        "lavfi.signalstats.YAVG=100\n"
        "lavfi.signalstats.VAVG=204\n"
    )
    fake = MockFfmpegRunner({0: stats_content})
    from flashframe.extract import run_extraction
    
    with patch("flashframe.extract.subprocess.run", fake):
        scan_id1 = run_extraction("vid.mp4", frame_start=None)
        
    with open("frame_metrics.csv") as f:
        lines = f.read().splitlines()
    assert lines[0] == "scan_id,frame_idx,pts_time,tile,yavg,ymax,ymin,satavg,red_ratio"
    # frame_idx for pts_time=0.2, frame_start=None is int(0.2*25.0) = 5
    # red_ratio for 204 is 0.8
    # yavg = 100, defaults = 0
    assert lines[1] == f"{scan_id1},5,0.2,0,100.0,0.0,0.0,0.0,0.8"

    # Now with frame_start = 100
    with patch("flashframe.extract.subprocess.run", fake):
        scan_id2 = run_extraction("vid.mp4", frame_start=100, frame_end=124)
        
    with open("frame_metrics.csv") as f:
        lines = f.read().splitlines()
    assert lines[1] == f"{scan_id2},105,0.2,0,100.0,0.0,0.0,0.0,0.8"

def test_extract_parser_flush_and_guards(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    stats_content = (
        "frame:0    pts:0       pts_time:0.0\n"
        "lavfi.signalstats.YMAX=20\n"  # Guard test: missing YAVG entirely
        "frame:1    pts:1       pts_time:0.1\n"
        "lavfi.signalstats.YAVG=10\n"  # Frame A
        "frame:2    pts:2       pts_time:0.2\n"
        "lavfi.signalstats.YAVG=20\n"  # Frame B
        "frame:3    pts:3       pts_time:0.3\n"
        "lavfi.signalstats.YAVG=30\n"  # Frame C
        # EOF happens right here, frame C must be flushed!
    )
    fake = MockFfmpegRunner({0: stats_content})
    from flashframe.extract import run_extraction
    
    with patch("flashframe.extract.subprocess.run", fake):
        scan_id = run_extraction("vid.mp4", frame_start=None)
        
    with open("frame_metrics.csv") as f:
        lines = f.read().splitlines()
        
    assert len(lines) == 4  # Header + 3 valid frames
    assert lines[1].startswith(f"{scan_id},2,0.1,0,10.0")
    assert lines[2].startswith(f"{scan_id},5,0.2,0,20.0")
    assert lines[3].startswith(f"{scan_id},7,0.3,0,30.0")

    # Now verify the EOF guard: file ends without YAVG
    stats_content_2 = (
        "frame:0    pts:0       pts_time:0.0\n"
        "lavfi.signalstats.YMAX=20\n"
    )
    fake2 = MockFfmpegRunner({0: stats_content_2})
    with patch("flashframe.extract.subprocess.run", fake2):
        run_extraction("vid.mp4", frame_start=None)
        
    with open("frame_metrics.csv") as f:
        lines2 = f.read().splitlines()
    assert len(lines2) == 1 # Only header

def test_extract_parser_missing_files_and_defaults(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    
    # "have the fake write only stats_0.txt and stats_5.txt"
    stats_0 = "frame:0    pts:0       pts_time:0.0\nlavfi.signalstats.YAVG=111\n"
    stats_5 = (
        "frame:0    pts:0       pts_time:0.0\n"
        "lavfi.signalstats.YAVG=222\n"
        "lavfi.signalstats.YMAX=255\n"
        "lavfi.signalstats.YMIN=0\n"
        "lavfi.signalstats.SATAVG=128\n"
        "lavfi.signalstats.VAVG=127.5\n" # red_ratio will be 0.5
    )
    
    fake = MockFfmpegRunner({0: stats_0, 5: stats_5})
    from flashframe.extract import run_extraction
    
    with patch("flashframe.extract.subprocess.run", fake):
        scan_id = run_extraction("vid.mp4", frame_start=None)
        
    with open("frame_metrics.csv") as f:
        lines = f.read().splitlines()
        
    assert len(lines) == 3
    # tile 0: defaults
    assert lines[1] == f"{scan_id},0,0.0,0,111.0,0.0,0.0,0.0,0.0"
    # tile 5: full
    assert lines[2] == f"{scan_id},0,0.0,5,222.0,255.0,0.0,128.0,0.5"

class FakeSubprocessRun:
    def __init__(self, expected_bytes=b"clipbytes"):
        self.expected_bytes = expected_bytes
        self.calls = []
        
    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        with open("span.mp4", "wb") as f:
            f.write(self.expected_bytes)

class FakeGenAIClient:
    def __init__(self, responses, assert_func=None):
        self.responses = responses
        self.assert_func = assert_func
        self.calls = []
        self.keys_used = []
        
    def __call__(self, api_key=None):
        self.keys_used.append(api_key)
        client_mock = MagicMock()
        
        def generate_content(*args, **kwargs):
            self.calls.append(kwargs)
            if self.assert_func:
                self.assert_func(kwargs)
            
            resp_spec = self.responses.pop(0)
            if isinstance(resp_spec, Exception):
                raise resp_spec
            
            mock_resp = MagicMock()
            mock_resp.text = resp_spec
            return mock_resp
            
        client_mock.models.generate_content = generate_content
        return client_mock

def test_adjudicate_explicit_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    
    fake_sp = FakeSubprocessRun()
    monkeypatch.setattr("flashframe.adjudicate.subprocess.run", fake_sp)
    
    valid_json = '{"passed": true, "frame_start": 10, "frame_end": 20, "measured_value": 2.5, "threshold_value": 3.0, "cause": "test", "remediation": "test"}'
    fake_client = FakeGenAIClient([valid_json])
    monkeypatch.setattr("flashframe.adjudicate.genai.Client", fake_client)
    
    from flashframe.adjudicate import run_adjudicate
    res = run_adjudicate("vid.mp4", 10, 20, api_key="explicit_key")
    
    assert fake_client.keys_used == ["explicit_key"]
    assert res.passed is True



def test_adjudicate_single_key_and_model(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "single_key")
    monkeypatch.setenv("GEMINI_MODEL", "my-test-model")
    
    fake_sp = FakeSubprocessRun()
    monkeypatch.setattr("flashframe.adjudicate.subprocess.run", fake_sp)
    
    valid_json = '{"passed": true, "frame_start": 10, "frame_end": 20, "measured_value": 2.5, "threshold_value": 3.0, "cause": "test", "remediation": "test"}'
    
    def assert_model(kwargs):
        assert kwargs["model"] == "my-test-model"
        
    fake_client = FakeGenAIClient([valid_json], assert_func=assert_model)
    monkeypatch.setattr("flashframe.adjudicate.genai.Client", fake_client)
    
    from flashframe.adjudicate import run_adjudicate
    res = run_adjudicate("vid.mp4", 10, 20)
    
    assert fake_client.keys_used == ["single_key"]
    assert res.passed is True

def test_adjudicate_credentials_file(monkeypatch, tmp_path):
    import json
    import os
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    
    cred_dir = tmp_path / ".config" / "gemini"
    cred_dir.mkdir(parents=True)
    cred_path = cred_dir / "credentials.json"
    
    creds = {
        "keys": [{"key": "key0"}, {"key": "key1_target"}, {"key": "key2"}],
        "model": "model-from-file"
    }
    with open(cred_path, "w") as f:
        json.dump(creds, f)
        
    def mock_expanduser(path):
        if path == '~/.config/gemini/credentials.json':
            return str(cred_path)
        return os.path.expanduser_orig(path)
        
    monkeypatch.setattr(os.path, 'expanduser_orig', os.path.expanduser, raising=False)
    monkeypatch.setattr(os.path, 'expanduser', mock_expanduser)
    
    fake_sp = FakeSubprocessRun()
    monkeypatch.setattr("flashframe.adjudicate.subprocess.run", fake_sp)
    
    valid_json = '{"passed": true, "frame_start": 10, "frame_end": 20, "measured_value": 2.5, "threshold_value": 3.0, "cause": "test", "remediation": "test"}'
    
    def assert_model(kwargs):
        assert kwargs["model"] == "model-from-file"
        
    fake_client = FakeGenAIClient([valid_json], assert_func=assert_model)
    monkeypatch.setattr("flashframe.adjudicate.genai.Client", fake_client)
    
    from flashframe.adjudicate import run_adjudicate
    run_adjudicate("vid.mp4", 10, 20)
    
    assert fake_client.keys_used == ["key1_target"]

def test_adjudicate_no_credentials(monkeypatch, tmp_path):
    import os
    import pytest
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    
    def mock_expanduser(path):
        if path == '~/.config/gemini/credentials.json':
            return str(tmp_path / "nonexistent.json")
        return path
        
    monkeypatch.setattr(os.path, 'expanduser', mock_expanduser)
    
    from flashframe.adjudicate import run_adjudicate
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY environment variable is missing"):
        run_adjudicate("vid.mp4", 10, 20)

def test_adjudicate_span_ffmpeg_and_request(monkeypatch, tmp_path):
    import os
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "key")
    
    with open("span.mp4", "w") as f:
        f.write("stale data")
        
    expected_bytes = b"new_clip_bytes"
    
    class AssertingSubprocessRun:
        def __call__(self, cmd, **kwargs):
            assert not os.path.exists("span.mp4")
            with open("span.mp4", "wb") as f:
                f.write(expected_bytes)
                
            assert cmd[0] == 'ffmpeg'
            assert '-ss' in cmd
            assert cmd[cmd.index('-ss') + 1] == str(10 / 25.0)
            assert '-t' in cmd
            assert cmd[cmd.index('-t') + 1] == str((20 - 10 + 1) / 25.0)
            assert '-c:v' in cmd
            assert cmd[cmd.index('-c:v') + 1] == 'libx264'
            assert kwargs.get("check") is True
            
    fake_sp = AssertingSubprocessRun()
    monkeypatch.setattr("flashframe.adjudicate.subprocess.run", fake_sp)
    
    from flashframe.adjudicate import Verdict
    def assert_request(kwargs):
        contents = kwargs["contents"]
        blob_part = contents[0]
        text_part = contents[1]
        
        assert blob_part.inline_data.data == expected_bytes
        assert blob_part.video_metadata.fps == 24
        assert "10" in text_part.text
        assert "20" in text_part.text
        
        cfg = kwargs["config"]
        assert cfg.temperature == 0.0
        assert cfg.response_mime_type == "application/json"
        assert cfg.response_schema == Verdict
        
    valid_json = '{"passed": true, "frame_start": 10, "frame_end": 20, "measured_value": 2.5, "threshold_value": 3.0, "cause": "test", "remediation": "test"}'
    fake_client = FakeGenAIClient([valid_json], assert_func=assert_request)
    monkeypatch.setattr("flashframe.adjudicate.genai.Client", fake_client)
    
    from flashframe.adjudicate import run_adjudicate
    res = run_adjudicate("vid.mp4", 10, 20)
    assert res.passed is True



def test_adjudicate_non_429_reraises(monkeypatch, tmp_path):
    import pytest
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "k1")
    
    fake_sp = FakeSubprocessRun()
    monkeypatch.setattr("flashframe.adjudicate.subprocess.run", fake_sp)
    
    from google.genai import errors
    err500 = errors.APIError(500, "Internal Server Error")
    fake_client = FakeGenAIClient([err500])
    monkeypatch.setattr("flashframe.adjudicate.genai.Client", fake_client)
    
    from flashframe.adjudicate import run_adjudicate
    with pytest.raises(errors.APIError) as excinfo:
        run_adjudicate("vid.mp4", 10, 20)
        
    assert fake_client.keys_used == ["k1"]
    assert len(fake_client.keys_used) == 1
    assert "Internal Server Error" in str(excinfo.value)



def test_seed_feature(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    import subprocess
    import random
    from click.testing import CliRunner
    from flashframe.cli import cli

    # 1. Patch random.randint to a fixed value.
    monkeypatch.setattr(random, "randint", lambda a, b: 100) # Say frame 100

    # 2. Patch subprocess.run
    called_args = []
    def mock_run(args, check=False):
        called_args.append((args, check))
    monkeypatch.setattr(subprocess, "run", mock_run)

    # 3. Run the command using CliRunner
    runner = CliRunner()
    result = runner.invoke(cli, ["seed", "--feature"])

    # 4. Assert exit code and output
    assert result.exit_code == 0
    assert "Generating feature-length benchmark clip..." in result.output
    assert "Chosen offset" in result.output
    assert "Feature clip generated: bench_feature.mp4" in result.output

    # 5. Assert manifest.json contents
    import json
    with open("manifest.json", "r") as f:
        manifest = json.load(f)
    
    assert manifest["strobe_start_frame"] == 100
    assert manifest["strobe_start_time"] == 100 / 25.0
    assert manifest["strobe_duration_frames"] == int(0.88 * 25)

    # 6. Assert generate_feature.sh segment math
    with open("generate_feature.sh", "r") as f:
        script = f.read()
    
    import re
    durations = re.findall(r'd=([0-9.]+)', script)
    assert len(durations) == 3
    seg_a = float(durations[0])
    seg_b = float(durations[1])
    seg_c = float(durations[2])
    
    import math
    assert math.isclose(seg_a + seg_b + seg_c, 5529.6, abs_tol=1e-5)
    assert seg_b == 0.88

    # 7. Assert subprocess was called correctly
    assert len(called_args) == 1
    assert called_args[0][0] == ["bash", "generate_feature.sh"]
    assert called_args[0][1] is True

def test_seed_default(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    import subprocess
    import os
    from click.testing import CliRunner
    from flashframe.cli import cli
    
    called_args = []
    def mock_run(args, check=False):
        called_args.append((args, check))
    monkeypatch.setattr(subprocess, "run", mock_run)
    
    runner = CliRunner()
    result = runner.invoke(cli, ["seed"])
    
    assert result.exit_code == 0
    assert "Running seed clip generation..." in result.output
    assert "Clips generated." in result.output
    
    assert len(called_args) == 1
    cmd = called_args[0][0]
    assert cmd[0] == "bash"
    script_path = cmd[1]
    
    import flashframe.cli
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(flashframe.cli.__file__)))
    expected_path = os.path.join(repo_root, "generate_seed_clips.sh")
    assert script_path == expected_path
    
    # Assert path exists on disk
    assert os.path.exists(script_path)
    assert called_args[0][1] is True



class PipelineHarness:
    def __init__(self):
        self.agent_kwargs = None
        self.detect_result = "[]"
        self.runner_kwargs = None
        self.mcp_params = None

@pytest.fixture
def harness(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    
    h = PipelineHarness()
    
    class FakeMcpToolset:
        def __init__(self, connection_params):
            h.mcp_params = connection_params
        async def get_tools(self):
            class DummyTool:
                name = "run_query"
            return [DummyTool()]
            
    class FakeLlmAgent:
        def __init__(self, **kwargs):
            h.agent_kwargs = kwargs
            
    class FakeRunner:
        def __init__(self, **kwargs):
            h.runner_kwargs = kwargs
        async def run_async(self, **kwargs):
            # Record kwargs passed to run_async
            h.runner_kwargs.update(kwargs)
            class FakeEvent:
                tool_call = True
            yield FakeEvent()
            
    def fake_extraction(video_path, scan_id=None, **kwargs):
        return "test_scan_id"
        
    async def fake_setup(tool, scan_id, video_path, src_fps, tgt_fps):
        pass
        
    async def fake_detect(tool, scan_id, fps):
        return h.detect_result

    monkeypatch.setattr("flashframe.cli.McpToolset", FakeMcpToolset)
    monkeypatch.setattr("flashframe.cli.LlmAgent", FakeLlmAgent)
    monkeypatch.setattr("flashframe.cli.Runner", FakeRunner)
    monkeypatch.setattr("flashframe.cli.run_extraction", fake_extraction)
    monkeypatch.setattr("flashframe.cli.setup_db_and_ingest", fake_setup)
    monkeypatch.setattr("flashframe.cli.detect_violations", fake_detect)
    
    return h

@pytest.mark.asyncio
async def test_pipeline_missing_credentials(harness, monkeypatch):
    from flashframe.cli import run_pipeline
    monkeypatch.delenv("CLICKHOUSE_HOST", raising=False)
    monkeypatch.delenv("CLICKHOUSE_USER", raising=False)
    monkeypatch.delenv("CLICKHOUSE_PASSWORD", raising=False)
    
    with pytest.raises(RuntimeError) as exc:
        await run_pipeline("vid.mp4")
        
    msg = str(exc.value)
    assert "CLICKHOUSE_HOST" in msg
    assert "CLICKHOUSE_USER" in msg
    assert "CLICKHOUSE_PASSWORD" in msg

@pytest.mark.asyncio
async def test_pipeline_mcp_env(harness, monkeypatch, tmp_path):
    from flashframe.cli import run_pipeline
    monkeypatch.setenv("CLICKHOUSE_HOST", "localhost")
    monkeypatch.setenv("CLICKHOUSE_USER", "default")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "pass")
    
    monkeypatch.delenv("CLICKHOUSE_DATABASE", raising=False)
    
    gemini_dir = tmp_path / ".config" / "gemini"
    gemini_dir.mkdir(parents=True, exist_ok=True)
    with open(gemini_dir / "credentials.json", "w") as f:
        json.dump({"keys": [{}, {}, {}, {"key": "test_key"}]}, f)

    await run_pipeline("vid.mp4")
    env = harness.mcp_params.server_params.env
    assert env["CLICKHOUSE_DATABASE"] == "flashframe"
    assert env["CLICKHOUSE_ALLOW_WRITE_ACCESS"] == "true"
    assert env["CLICKHOUSE_ALLOW_DROP"] == "true"
    assert env["CHDB_ENABLED"] == "true"
    
    monkeypatch.setenv("CLICKHOUSE_DATABASE", "custom_db")
    await run_pipeline("vid.mp4")
    env2 = harness.mcp_params.server_params.env
    assert env2["CLICKHOUSE_DATABASE"] == "custom_db"

@pytest.mark.asyncio
async def test_pipeline_mcp_command(harness, monkeypatch, tmp_path):
    from flashframe.cli import run_pipeline
    monkeypatch.setenv("CLICKHOUSE_HOST", "localhost")
    monkeypatch.setenv("CLICKHOUSE_USER", "default")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "pass")
    monkeypatch.setenv("GEMINI_API_KEY", "env_key")
    
    await run_pipeline("vid.mp4")
    
    import sys
    assert harness.mcp_params.server_params.command == sys.executable

@pytest.mark.asyncio
async def test_pipeline_gemini_key_resolution_env(harness, monkeypatch, tmp_path):
    from flashframe.cli import run_pipeline
    monkeypatch.setenv("CLICKHOUSE_HOST", "localhost")
    monkeypatch.setenv("CLICKHOUSE_USER", "default")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "pass")
    
    monkeypatch.setenv("GEMINI_API_KEY", "env_key")
    monkeypatch.setenv("GEMINI_MODEL", "custom-model")
    harness.detect_result = '[{"frame_start": 10, "frame_end": 20, "flashes": 5.0}]'
    
    await run_pipeline("vid.mp4")
    assert harness.agent_kwargs["model"] == "custom-model"
    assert os.environ["GEMINI_API_KEY"] == "env_key"
    
@pytest.mark.asyncio
async def test_pipeline_gemini_key_resolution_file(harness, monkeypatch, tmp_path):
    from flashframe.cli import run_pipeline
    monkeypatch.setenv("CLICKHOUSE_HOST", "localhost")
    monkeypatch.setenv("CLICKHOUSE_USER", "default")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "pass")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    
    harness.detect_result = '[{"frame_start": 10, "frame_end": 20, "flashes": 5.0}]'
    
    gemini_dir = tmp_path / ".config" / "gemini"
    gemini_dir.mkdir(parents=True, exist_ok=True)
    with open(gemini_dir / "credentials.json", "w") as f:
        json.dump({
            "keys": [{"key": "k0"}, {"key": "k1"}, {"key": "k2"}, {"key": "k3"}],
            "model": "file-model"
        }, f)
        
    await run_pipeline("vid.mp4")
    
    assert os.environ["GEMINI_API_KEY"] == "k3"
    assert harness.agent_kwargs["model"] == "file-model"
    
@pytest.mark.asyncio
async def test_pipeline_gemini_key_resolution_missing(harness, monkeypatch, tmp_path):
    from flashframe.cli import run_pipeline
    monkeypatch.setenv("CLICKHOUSE_HOST", "localhost")
    monkeypatch.setenv("CLICKHOUSE_USER", "default")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "pass")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY environment variable is missing and fallback"):
        await run_pipeline("vid.mp4")

@pytest.mark.asyncio
async def test_pipeline_span_parsing_list_of_dicts(harness, monkeypatch, capsys):
    from flashframe.cli import run_pipeline
    monkeypatch.setenv("CLICKHOUSE_HOST", "h")
    monkeypatch.setenv("CLICKHOUSE_USER", "u")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "p")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    
    harness.detect_result = '[{"frame_start": 100, "frame_end": 120, "flashes": 4.5}]'
    await run_pipeline("vid.mp4")
    
    out = capsys.readouterr().out
    assert "Flagged span 100-120 (4.5 flashes/sec)" in out
    
    assert "frame_start: 100" in harness.runner_kwargs["new_message"].parts[0].text
    assert "frame_end: 120" in harness.runner_kwargs["new_message"].parts[0].text

@pytest.mark.asyncio
async def test_pipeline_span_parsing_dict(harness, monkeypatch, capsys):
    from flashframe.cli import run_pipeline
    monkeypatch.setenv("CLICKHOUSE_HOST", "h")
    monkeypatch.setenv("CLICKHOUSE_USER", "u")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "p")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    
    harness.detect_result = '{"frame_start": 101, "frame_end": 121, "flashes": 4.6}'
    await run_pipeline("vid.mp4")
    
    out = capsys.readouterr().out
    assert "Flagged span 101-121 (4.6 flashes/sec)" in out

@pytest.mark.asyncio
async def test_pipeline_span_parsing_list_of_lists(harness, monkeypatch, capsys):
    from flashframe.cli import run_pipeline
    monkeypatch.setenv("CLICKHOUSE_HOST", "h")
    monkeypatch.setenv("CLICKHOUSE_USER", "u")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "p")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    
    harness.detect_result = '[[102, 122, 4.7, 0.9, 0]]'
    await run_pipeline("vid.mp4")
    
    out = capsys.readouterr().out
    assert "Flagged span 102-122 (4.7 flashes/sec)" in out

@pytest.mark.asyncio
async def test_pipeline_span_parsing_unparseable(harness, monkeypatch, capsys):
    from flashframe.cli import run_pipeline
    monkeypatch.setenv("CLICKHOUSE_HOST", "h")
    monkeypatch.setenv("CLICKHOUSE_USER", "u")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "p")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    
    harness.detect_result = 'not json'
    await run_pipeline("vid.mp4")
    
    out = capsys.readouterr().out
    assert "No violations detected. PASS." in out
    assert harness.agent_kwargs is None

@pytest.mark.asyncio
async def test_pipeline_span_parsing_empty(harness, monkeypatch, capsys):
    from flashframe.cli import run_pipeline
    monkeypatch.setenv("CLICKHOUSE_HOST", "h")
    monkeypatch.setenv("CLICKHOUSE_USER", "u")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "p")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    
    harness.detect_result = '[]'
    await run_pipeline("vid.mp4")
    
    out = capsys.readouterr().out
    assert "No violations detected. PASS." in out
    assert harness.agent_kwargs is None

@pytest.mark.asyncio
async def test_pipeline_agent_wiring(harness, monkeypatch, tmp_path):
    from flashframe.cli import run_pipeline
    monkeypatch.setenv("CLICKHOUSE_HOST", "h")
    monkeypatch.setenv("CLICKHOUSE_USER", "u")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "p")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_MODEL", "my-test-model")
    
    harness.detect_result = '{"frame_start": 200, "frame_end": 220, "flashes": 5.5}'
    await run_pipeline("vid.mp4")
    
    assert harness.agent_kwargs is not None
    assert harness.agent_kwargs["model"] == "my-test-model"
    tools = harness.agent_kwargs["tools"]
    assert len(tools) == 3
    tool_names = [t.func.__name__ for t in tools]
    assert "resample_frames" in tool_names
    assert "final_adjudicate" in tool_names
    assert "certify" in tool_names
    
    assert harness.runner_kwargs is not None
    assert harness.runner_kwargs["app_name"] == "flashframe"
    msg = harness.runner_kwargs["new_message"]
    text = msg.parts[0].text
    assert "scan_id: test_scan_id" in text
    assert "frame_start: 200" in text
    assert "frame_end: 220" in text



def test_cli_pipeline_command(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    from flashframe.cli import cli
    from click.testing import CliRunner
    
    passed_args = []
    async def mock_run_pipeline(video_path):
        passed_args.append(video_path)
        
    with patch("flashframe.cli.run_pipeline", mock_run_pipeline):
        runner = CliRunner()
        result = runner.invoke(cli, ["pipeline", "my_test_vid.mp4"])
        
    assert result.exit_code == 0
    assert passed_args == ["my_test_vid.mp4"]

@pytest.mark.asyncio
async def test_resample_frames_cap_and_passthrough(harness, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    from flashframe.cli import run_pipeline
    monkeypatch.setenv("CLICKHOUSE_HOST", "h")
    monkeypatch.setenv("CLICKHOUSE_USER", "u")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "p")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    
    harness.detect_result = '{"frame_start": 200, "frame_end": 220, "flashes": 5.5}'
    
    extracted_args = []
    def fake_extraction2(video_path, fps_override=None, frame_start=None, frame_end=None, scan_id=None):
        extracted_args.append((video_path, fps_override, frame_start, frame_end))
        return "new_scan_id_123"
        
    monkeypatch.setattr("flashframe.cli.run_extraction", fake_extraction2)
    
    await run_pipeline("vid.mp4")
    
    tools = {t.func.__name__: t.func for t in harness.agent_kwargs["tools"]}
    resample_frames = tools["resample_frames"]
    
    extracted_args.clear()
    res1 = await resample_frames(frame_start=10, frame_end=20, target_fps=30)
    assert res1["status"] == "success"
    assert res1["new_scan_id"] == "new_scan_id_123"
    assert extracted_args == [("vid.mp4", 30, 10, 20)]
    
    extracted_args.clear()
    res2 = await resample_frames(frame_start=10, frame_end=20, target_fps=60)
    assert res2["status"] == "success"
    assert extracted_args == [("vid.mp4", 60, 10, 20)]
    
    extracted_args.clear()
    res3 = await resample_frames(frame_start=10, frame_end=20, target_fps=120)
    assert res3["status"] == "error"
    assert res3["message"] == "Max resample iterations reached."
    assert extracted_args == []

@pytest.mark.asyncio
async def test_resample_frames_rate_updates(harness, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    from flashframe.cli import run_pipeline
    monkeypatch.setenv("CLICKHOUSE_HOST", "h")
    monkeypatch.setenv("CLICKHOUSE_USER", "u")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "p")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    
    cert_args = []
    async def fake_write_certificate(tool, scan_id, passed, frame_start, frame_end, measured, cause, remediation, gem_rate):
        cert_args.append(measured)
        return {}
    monkeypatch.setattr("flashframe.certify.write_certificate", fake_write_certificate)

    harness.detect_result = '{"frame_start": 200, "frame_end": 220, "flashes": 5.5}'
    await run_pipeline("vid.mp4")
    tools = {t.func.__name__: t.func for t in harness.agent_kwargs["tools"]}
    harness.detect_result = '[{"flashes": 6.1}]'
    await tools["resample_frames"](frame_start=10, frame_end=20, target_fps=30)
    await tools["certify"]("s", True, 10, 20, "c", "r", 0.0)
    assert cert_args[-1] == 6.1

    harness.detect_result = '{"frame_start": 200, "frame_end": 220, "flashes": 5.5}'
    await run_pipeline("vid.mp4")
    tools = {t.func.__name__: t.func for t in harness.agent_kwargs["tools"]}
    harness.detect_result = '{"flashes": 7.2}'
    await tools["resample_frames"](frame_start=10, frame_end=20, target_fps=30)
    await tools["certify"]("s", True, 10, 20, "c", "r", 0.0)
    assert cert_args[-1] == 7.2
    
    harness.detect_result = '{"frame_start": 200, "frame_end": 220, "flashes": 5.5}'
    await run_pipeline("vid.mp4")
    tools = {t.func.__name__: t.func for t in harness.agent_kwargs["tools"]}
    harness.detect_result = '[[0, 1, 8.3]]'
    await tools["resample_frames"](frame_start=10, frame_end=20, target_fps=30)
    await tools["certify"]("s", True, 10, 20, "c", "r", 0.0)
    assert cert_args[-1] == 8.3

    harness.detect_result = '{"frame_start": 200, "frame_end": 220, "flashes": 5.5}'
    await run_pipeline("vid.mp4")
    tools = {t.func.__name__: t.func for t in harness.agent_kwargs["tools"]}
    harness.detect_result = 'not json'
    await tools["resample_frames"](frame_start=10, frame_end=20, target_fps=30)
    await tools["certify"]("s", True, 10, 20, "c", "r", 0.0)
    assert cert_args[-1] == 5.5

    harness.detect_result = '{"frame_start": 200, "frame_end": 220, "flashes": 5.5}'
    await run_pipeline("vid.mp4")
    tools = {t.func.__name__: t.func for t in harness.agent_kwargs["tools"]}
    await tools["resample_frames"](frame_start=10, frame_end=20, target_fps=30)
    await tools["resample_frames"](frame_start=10, frame_end=20, target_fps=30)
    res = await tools["resample_frames"](frame_start=10, frame_end=20, target_fps=30)
    assert res == {"status": "error", "message": "Max resample iterations reached."}

@pytest.mark.asyncio
async def test_final_adjudicate_happy_path_and_provenance(harness, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    from flashframe.cli import run_pipeline
    monkeypatch.setenv("CLICKHOUSE_HOST", "h")
    monkeypatch.setenv("CLICKHOUSE_USER", "u")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "p")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    harness.detect_result = '{"frame_start": 200, "frame_end": 220, "flashes": 5.5}'
    await run_pipeline("vid.mp4")
    
    tools = {t.func.__name__: t.func for t in harness.agent_kwargs["tools"]}
    final_adjudicate = tools["final_adjudicate"]
    
    class FakeVerdict:
        passed = True
        cause = "test_cause"
        remediation = "test_rem"
        measured_value = 1.23
        frame_start = 10
        frame_end = 20

    def fake_run_adjudicate(*args, **kwargs):
        return FakeVerdict()
        
    monkeypatch.setattr("flashframe.adjudicate.run_adjudicate", fake_run_adjudicate)
    
    res = final_adjudicate(frame_start=10, frame_end=20)
    assert res["gemini_estimated_rate"] == 1.23
    assert res["passed"] is True
    assert res["cause"] == "test_cause"
    assert res["remediation"] == "test_rem"
    assert res["frame_start"] == 10
    assert res["frame_end"] == 20
    
    for k in res.keys():
        assert "measured" not in k.lower()

@pytest.mark.asyncio
async def test_final_adjudicate_retries_and_errors(harness, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    from flashframe.cli import run_pipeline
    monkeypatch.setenv("CLICKHOUSE_HOST", "h")
    monkeypatch.setenv("CLICKHOUSE_USER", "u")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "p")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    harness.detect_result = '{"frame_start": 200, "frame_end": 220, "flashes": 5.5}'
    await run_pipeline("vid.mp4")
    
    tools = {t.func.__name__: t.func for t in harness.agent_kwargs["tools"]}
    final_adjudicate = tools["final_adjudicate"]
    
    from google.genai.errors import APIError
    
    def fake_run_adjudicate_500(*args, **kwargs):
        raise APIError(500, {"error": {"message": "500 err", "status": "INTERNAL"}})
    
    monkeypatch.setattr("flashframe.adjudicate.run_adjudicate", fake_run_adjudicate_500)
    with pytest.raises(APIError):
        final_adjudicate(frame_start=10, frame_end=20)
        
    sleeps = []
    def fake_sleep(s):
        sleeps.append(s)
        
    monkeypatch.setattr("time.sleep", fake_sleep)
    
    attempt_count = 0
    def fake_run_adjudicate_retry(*args, **kwargs):
        nonlocal attempt_count
        attempt_count += 1
        raise APIError(429, {"error": {"message": "rate limit", "status": "RESOURCE_EXHAUSTED"}})
        
    monkeypatch.setattr("flashframe.adjudicate.run_adjudicate", fake_run_adjudicate_retry)
    
    res = final_adjudicate(frame_start=10, frame_end=20)
    assert res == {"error": "Exceeded maximum retries for Gemini API"}
    assert attempt_count == 5
    assert sleeps == [20, 40, 60, 80, 100]

@pytest.mark.asyncio
async def test_certify_args_and_output(harness, monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    from flashframe.cli import run_pipeline
    monkeypatch.setenv("CLICKHOUSE_HOST", "h")
    monkeypatch.setenv("CLICKHOUSE_USER", "u")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "p")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    harness.detect_result = '{"frame_start": 200, "frame_end": 220, "flashes": 4.5678}'
    await run_pipeline("vid.mp4")
    
    tools = {t.func.__name__: t.func for t in harness.agent_kwargs["tools"]}
    certify = tools["certify"]
    
    called_args = {}
    async def fake_write_certificate(tool, scan_id, passed, frame_start, frame_end, measured, cause, remediation, gem_rate):
        called_args.update({
            "measured": measured,
            "gem_rate": gem_rate
        })
        return {"cert": "fake"}
        
    monkeypatch.setattr("flashframe.certify.write_certificate", fake_write_certificate)
    
    res = await certify("scan1", True, 200, 220, "test_cause", "test_rem", 1.23)
    assert res == {"cert": "fake"}
    
    assert called_args["measured"] == 4.5678
    assert called_args["gem_rate"] == 1.23
    
    out = capsys.readouterr().out
    assert "MEASURED (ClickHouse)          4.57 flashes/sec" in out
    assert "ADJUDICATED (Gemini)           PASS — test_cause" in out

def test_run_extraction_honours_supplied_scan_id(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    fake = MockFfmpegRunner()
    from flashframe.extract import run_extraction
    import uuid
    
    with patch("flashframe.extract.subprocess.run", fake):
        # Without scan_id, generates fresh UUID
        scan_id_fresh = run_extraction("vid.mp4", fps_override=10)
        assert isinstance(uuid.UUID(scan_id_fresh), uuid.UUID)
        
        # With scan_id, returns it
        scan_id_supplied = run_extraction("vid.mp4", fps_override=10, scan_id="fixed-id")
        assert scan_id_supplied == "fixed-id"
