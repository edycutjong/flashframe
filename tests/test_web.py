import pytest
from fastapi.testclient import TestClient
import os
import json
import base64
from unittest.mock import MagicMock, AsyncMock
import tempfile
import asyncio
import subprocess

ORIGINAL_DIR = os.path.abspath(os.getcwd())

import web
from web import app, scan_status_dict

@pytest.fixture(autouse=True)
def setup_env(monkeypatch, tmp_path):
    try:
        os.symlink(os.path.join(ORIGINAL_DIR, "templates"), tmp_path / "templates")
        os.symlink(os.path.join(ORIGINAL_DIR, "assets"), tmp_path / "assets")
        os.symlink(os.path.join(ORIGINAL_DIR, "thresholds.csv"), tmp_path / "thresholds.csv")
    except FileExistsError:
        pass
        
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLICKHOUSE_HOST", "fake")
    monkeypatch.setenv("CLICKHOUSE_USER", "fake")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "fake")
    scan_status_dict.clear()

GLOBAL_TOOLS = []

class MockMcpToolset:
    def __init__(self, *args, **kwargs):
        pass
    async def get_tools(self):
        return GLOBAL_TOOLS

@pytest.fixture
def mock_mcp(monkeypatch):
    global GLOBAL_TOOLS
    GLOBAL_TOOLS = []
    monkeypatch.setattr("web.McpToolset", MockMcpToolset)
    monkeypatch.setattr("web.run_pipeline", AsyncMock())
    return MockMcpToolset

@pytest.fixture
def client(mock_mcp):
    with TestClient(app) as c:
        yield c

class MockTool:
    def __init__(self, name, returns=None, side_effect=None):
        self.name = name
        self.returns = returns or '{"rows": [], "columns": []}'
        self.side_effect = side_effect

    async def run_async(self, args, tool_context=None):
        if self.side_effect:
            if isinstance(self.side_effect, Exception):
                raise self.side_effect
            return self.side_effect(args)
        class Content:
            def __init__(self, text):
                self.text = text
        class Res:
            def __init__(self, text):
                self.content = [Content(text)]
        return Res(self.returns)

def test_read_index(client):
    response = client.get("/")
    assert response.status_code == 200

def test_lifespan_missing_creds(monkeypatch):
    monkeypatch.delenv("CLICKHOUSE_HOST", raising=False)
    with pytest.raises(RuntimeError, match="Missing required ClickHouse credentials"):
        with TestClient(app):
            pass

def test_lifespan_warmup_error(monkeypatch):
    class BrokenMcp:
        def __init__(self, *args, **kwargs):
            pass
        async def get_tools(self):
            raise Exception("Warmup failed")
    monkeypatch.setattr("web.McpToolset", BrokenMcp)
    with TestClient(app):
        pass

def test_404(client):
    res = client.get("/nonexistent")
    assert res.status_code == 404

    assert b"The requested page or scan results could not be found." in res.content

def test_other_http_exception(client):
    res = client.post("/")
    assert res.status_code == 405

def test_upload_no_file(client):
    res = client.post("/upload")
    assert res.status_code == 400
    assert b"No video was provided." in res.content

def test_upload_seed_clip(client):
    res = client.post("/upload", data={"seed_clip": "test_clip"}, follow_redirects=False)
    assert res.status_code == 303
    scan_id = res.headers["location"].split("/")[-1]
    assert scan_id in scan_status_dict
    assert web.run_pipeline.call_args[0][0] == "assets/test_clip.mp4"
    assert scan_status_dict[scan_id]["status"] == "complete"

def test_upload_file(client):
    res = client.post("/upload", files={"file": ("test.mp4", b"dummy content")}, follow_redirects=False)
    assert res.status_code == 303
    scan_id = res.headers["location"].split("/")[-1]
    assert web.run_pipeline.call_args[0][0] == f"{scan_id}_test.mp4"
    assert os.path.exists(f"{scan_id}_test.mp4")

def test_run_actual_pipeline_error_generic(client):
    web.run_pipeline.side_effect = Exception("Generic error")
    res = client.post("/upload", data={"seed_clip": "test_clip"}, follow_redirects=False)
    scan_id = res.headers["location"].split("/")[-1]
    assert scan_status_dict[scan_id]["status"] == "error"
    assert scan_status_dict[scan_id]["error_message"] == "Generic error"

def test_run_actual_pipeline_error_ratelimit(client):
    web.run_pipeline.side_effect = Exception("ResourceExhausted")
    res = client.post("/upload", data={"seed_clip": "test_clip"}, follow_redirects=False)
    scan_id = res.headers["location"].split("/")[-1]
    assert scan_status_dict[scan_id]["status"] == "error"
    assert "rate limit exceeded" in scan_status_dict[scan_id]["error_message"]

def test_scan_progress(client):
    res = client.get("/scan/1234")
    assert res.status_code == 200

def test_scan_status(client):
    res = client.get("/api/scan/unknown/status")
    assert res.json()["status"] == "error"
    
    scan_status_dict["known"] = {"status": "complete"}
    res2 = client.get("/api/scan/known/status")
    assert res2.json()["status"] == "complete"

class MockQueryTool:
    def __init__(self, cert_ret, metrics_ret, meta_ret, cert_type="std", metrics_type="std", meta_type="std"):
        self.cert_ret = cert_ret
        self.metrics_ret = metrics_ret
        self.meta_ret = meta_ret
        self.cert_type = cert_type
        self.metrics_type = metrics_type
        self.meta_type = meta_type
        self.name = "run_query"
        
    async def run_async(self, args, tool_context=None):
        query = args["query"]
        print(f"MOCK QUERY: {query}")
        if "violation_ledger" in query:
            if isinstance(self.cert_ret, Exception): raise self.cert_ret
            print(f"MOCK RETURNING: {self.cert_ret}")
            return self._format(self.cert_ret, self.cert_type)
        if "frame_metrics" in query:
            if isinstance(self.metrics_ret, Exception): raise self.metrics_ret
            return self._format(self.metrics_ret, self.metrics_type)
        if "scan_metadata" in query:
            if isinstance(self.meta_ret, Exception): raise self.meta_ret
            return self._format(self.meta_ret, self.meta_type)
        return self._format({}, "std")
            
    def _format(self, data, fmt_type):
        text = json.dumps(data) if not isinstance(data, str) else data
        if fmt_type == "std":
            class Content:
                def __init__(self, text): self.text = text
            class Res:
                def __init__(self, text): self.content = [Content(text)]
            return Res(text)
        elif fmt_type == "text":
            class Res:
                def __init__(self, text): self.text = text
            return Res(text)
        elif fmt_type == "list":
            class Res:
                def __init__(self, text): self.text = text
            return [Res(text)]
        elif fmt_type == "dict":
            return {"content": [{"text": text}]}
        else:
            return text

def test_report_success_all_formats(client):
    global GLOBAL_TOOLS
    GLOBAL_TOOLS = [MockQueryTool(
        cert_ret={"rows": [["2024-01-01", True, 0, 0, 0.0, 3.0, "", ""]], "columns": ["certified_at", "passed", "frame_start", "frame_end", "measured", "threshold", "cause", "remediation"]},
        metrics_ret=[{"frame_idx": 0, "yavg": 100}, {"frame_idx": 1, "yavg": 150}],
        meta_ret={"rows": [{"source_file": "test_clip.mp4", "source_fps": 30.0, "measured_fps": 30.0}]},
        cert_type="std", metrics_type="text", meta_type="list"
    )]
    res = client.get("/report/1234")
    assert res.status_code == 200

def test_report_formats_dict_and_str(client):
    global GLOBAL_TOOLS
    GLOBAL_TOOLS = [MockQueryTool(
        cert_ret=[{"passed": True, "measured": 3.5, "threshold": 3.0, "frame_start":0, "frame_end":0}],
        metrics_ret={"rows": [[0, 100], [1, 150]], "columns": ["f", "y"]},
        meta_ret={"source_file": "unknown"},
        cert_type="dict", metrics_type="str", meta_type="dict"
    )]
    res = client.get("/report/1234")
    assert res.status_code == 200

def test_report_no_data_and_exceptions(client):
    global GLOBAL_TOOLS
    GLOBAL_TOOLS = [MockQueryTool(
        cert_ret=Exception("DB down"),
        metrics_ret=Exception("DB down"),
        meta_ret=Exception("DB down")
    )]
    res = client.get("/report/1234")
    assert res.status_code == 200

def test_report_cert_not_found(client):
    global GLOBAL_TOOLS
    GLOBAL_TOOLS = [MockQueryTool(
        cert_ret={"rows": [], "columns": []},
        metrics_ret=[],
        meta_ret={}
    )]
    res = client.get("/report/1234")
    assert res.status_code == 404


def test_report_failed_cert_ffmpeg_success(client, monkeypatch, tmp_path):
    fake_vid = tmp_path / "fake_vid.mp4"
    fake_vid.touch()
    
    global GLOBAL_TOOLS
    GLOBAL_TOOLS = [MockQueryTool(
        cert_ret=[{"passed": False, "frame_start": 0, "frame_end": 10, "measured": 5.0}],
        metrics_ret=[{"frame_idx": 0, "yavg": 100}],
        meta_ret=[{"source_file": "fake_vid.mp4", "source_fps": 30.0, "measured_fps": 30.0}]
    )]
    
    def fake_run(cmd, *args, **kwargs):
        tmpdir = os.path.dirname(cmd[-1])
        with open(os.path.join(tmpdir, "frame_1.jpg"), "wb") as f:
            f.write(b"abc")
        return MagicMock()
        
    monkeypatch.setattr(subprocess, "run", fake_run)
    
    res = client.get("/report/1234")
    assert res.status_code == 200

def test_report_failed_cert_ffmpeg_failure(client, monkeypatch, tmp_path):
    fake_vid = tmp_path / "fake_vid.mp4"
    fake_vid.touch()
    
    global GLOBAL_TOOLS
    GLOBAL_TOOLS = [MockQueryTool(
        cert_ret=[{"passed": False, "frame_start": 0, "frame_end": 10}],
        metrics_ret=[{"frame_idx": 0, "yavg": 100}],
        meta_ret=[{"source_file": "fake_vid.mp4", "source_fps": 30.0, "measured_fps": 30.0}]
    )]
    
    def fake_run_err(*args, **kwargs):
        raise Exception("ffmpeg crashed")
        
    monkeypatch.setattr(subprocess, "run", fake_run_err)
    
    res = client.get("/report/1234")
    assert res.status_code == 200

def test_report_no_data_empty_df(client):
    global GLOBAL_TOOLS
    GLOBAL_TOOLS = [MockQueryTool(
        cert_ret=[{"passed": True}],
        metrics_ret=[],
        meta_ret=[]
    )]
    res = client.get("/report/1234")
    assert res.status_code == 200

def test_report_source_in_assets(client, monkeypatch, tmp_path):
    (tmp_path / "assets" / "existing_asset.mp4").touch()
    
    global GLOBAL_TOOLS
    GLOBAL_TOOLS = [MockQueryTool(
        cert_ret=[{"passed": False, "frame_start": 0, "frame_end": 10}],
        metrics_ret=[],
        meta_ret=[{"source_file": "existing_asset.mp4"}]
    )]
    
    def fake_run(cmd, *args, **kwargs):
        tmpdir = os.path.dirname(cmd[-1])
        with open(os.path.join(tmpdir, "frame_1.jpg"), "wb") as f:
            f.write(b"abc")
        return MagicMock()
    monkeypatch.setattr(subprocess, "run", fake_run)
    
    res = client.get("/report/1234")
    assert res.status_code == 200

def test_lifespan_warmup_run_async_error(monkeypatch):
    class BrokenMcpRunAsync:
        def __init__(self, *args, **kwargs):
            pass
        async def get_tools(self):
            class BrokenTool:
                name = "run_query"
                async def run_async(self, args, tool_context=None):
                    raise Exception("Run async warmup failed")
            return [BrokenTool()]
    monkeypatch.setattr("web.McpToolset", BrokenMcpRunAsync)
    with TestClient(app) as c:
        res = c.get("/")
        assert res.status_code == 200

def test_report_cert_shape_text(client):
    global GLOBAL_TOOLS
    GLOBAL_TOOLS = [MockQueryTool(
        cert_ret=[{"passed": False, "measured": 77.7, "threshold": 3.0, "frame_start":0, "frame_end":0}],
        metrics_ret=[],
        meta_ret=[],
        cert_type="text"
    )]
    res = client.get("/report/1234")
    assert b"77.7 Hz" in res.content

def test_report_cert_shape_list(client):
    global GLOBAL_TOOLS
    GLOBAL_TOOLS = [MockQueryTool(
        cert_ret=[{"passed": False, "measured": 88.8, "threshold": 3.0, "frame_start":0, "frame_end":0}],
        metrics_ret=[],
        meta_ret=[],
        cert_type="list"
    )]
    res = client.get("/report/1234")
    assert b"88.8 Hz" in res.content

def test_report_cert_shape_str(client):
    global GLOBAL_TOOLS
    GLOBAL_TOOLS = [MockQueryTool(
        cert_ret=[{"passed": False, "measured": 99.9, "threshold": 3.0, "frame_start":0, "frame_end":0}],
        metrics_ret=[],
        meta_ret=[],
        cert_type="str"
    )]
    res = client.get("/report/1234")
    assert b"99.9 Hz" in res.content

def test_report_metrics_shape_list(client):
    global GLOBAL_TOOLS
    GLOBAL_TOOLS = [MockQueryTool(
        cert_ret=[{"passed": True, "measured": 1.0, "threshold": 3.0, "frame_start":0, "frame_end":0}],
        metrics_ret=[{"frame_idx": 12345, "yavg": 100}],
        meta_ret=[],
        metrics_type="list"
    )]
    res = client.get("/report/1234")
    assert b"12345" in res.content

def test_report_metrics_shape_dict(client):
    global GLOBAL_TOOLS
    GLOBAL_TOOLS = [MockQueryTool(
        cert_ret=[{"passed": True, "measured": 1.0, "threshold": 3.0, "frame_start":0, "frame_end":0}],
        metrics_ret=[{"frame_idx": 54321, "yavg": 100}],
        meta_ret=[],
        metrics_type="dict"
    )]
    res = client.get("/report/1234")
    assert b"54321" in res.content

def test_report_meta_shape_text(client):
    global GLOBAL_TOOLS
    GLOBAL_TOOLS = [MockQueryTool(
        cert_ret=[{"passed": True}],
        metrics_ret=[],
        meta_ret=[{"source_file": "MetaText.mp4"}],
        meta_type="text"
    )]
    res = client.get("/report/1234")
    assert b"MetaText.mp4" in res.content

def test_report_meta_shape_str(client):
    global GLOBAL_TOOLS
    GLOBAL_TOOLS = [MockQueryTool(
        cert_ret=[{"passed": True}],
        metrics_ret=[],
        meta_ret=[{"source_file": "MetaStr.mp4"}],
        meta_type="str"
    )]
    res = client.get("/report/1234")
    assert b"MetaStr.mp4" in res.content

def test_report_meta_bare_list(client):
    global GLOBAL_TOOLS
    GLOBAL_TOOLS = [MockQueryTool(
        cert_ret=[{"passed": True}],
        metrics_ret=[],
        meta_ret=[{"source_file": "bare_list.mp4"}],
        meta_type="std"
    )]
    res = client.get("/report/1234")
    assert b"bare_list.mp4" in res.content

def test_report_meta_empty(client):
    global GLOBAL_TOOLS
    GLOBAL_TOOLS = [MockQueryTool(
        cert_ret=[{"passed": True}],
        metrics_ret=[],
        meta_ret=[],
        meta_type="std"
    )]
    res = client.get("/report/1234")
    assert b"hard_fail_strobe.mp4" in res.content

def test_report_source_unknown_fallback(client):
    global GLOBAL_TOOLS
    GLOBAL_TOOLS = [MockQueryTool(
        cert_ret=[{"passed": True}],
        metrics_ret=[],
        meta_ret=[{"source_file": "unknown"}]
    )]
    res = client.get("/report/1234")
    assert b"hard_fail_strobe.mp4" in res.content
    assert b"Source footage unavailable for inline preview." not in res.content

def test_report_filmstrip_unavailable_note(client):
    global GLOBAL_TOOLS
    GLOBAL_TOOLS = [MockQueryTool(
        cert_ret=[{"passed": False, "frame_start": 0, "frame_end": 10, "measured": 4.0}],
        metrics_ret=[],
        meta_ret=[{"source_file": "does_not_exist_at_all.mp4"}]
    )]
    res = client.get("/report/1234")
    assert b"Source footage unavailable for inline preview." in res.content


def test_report_meta_with_columns(client):
    global GLOBAL_TOOLS
    GLOBAL_TOOLS = [MockQueryTool(
        cert_ret=[{"passed": True}],
        metrics_ret=[],
        meta_ret={"columns": ["source_file", "source_fps"], "rows": [["col_test.mp4", 30.0]]},
        meta_type="std"
    )]
    res = client.get("/report/1234")
    assert b"col_test.mp4" in res.content

