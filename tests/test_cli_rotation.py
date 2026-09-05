import pytest
import os
from unittest.mock import patch, AsyncMock, MagicMock

from flashframe.cli import run_pipeline

@pytest.fixture(autouse=True)
def isolate_env():
    # Clean environment so we don't pick up real keys
    with patch.dict(os.environ, {
        "CLICKHOUSE_HOST": "dummy",
        "CLICKHOUSE_USER": "dummy",
        "CLICKHOUSE_PASSWORD": "dummy"
    }, clear=True):
        yield

@pytest.fixture
def mock_mcp():
    with patch('flashframe.cli.McpToolset') as m:
        mcp_instance = MagicMock()
        mock_tool = AsyncMock()
        mock_tool.name = "run_query"
        mcp_instance.get_tools = AsyncMock(return_value=[mock_tool])
        m.return_value = mcp_instance
        yield m

@pytest.fixture
def mock_pipeline_deps():
    with patch('flashframe.cli.run_extraction', return_value="scan_123"), \
         patch('flashframe.cli.setup_db_and_ingest', new_callable=AsyncMock), \
         patch('flashframe.cli.detect_violations', new_callable=AsyncMock, return_value='[{"frame_start": 0, "frame_end": 10, "flashes": 4.0}]'):
        yield

@pytest.fixture
def mock_runner():
    with patch('flashframe.cli.Runner') as m:
        yield m

@pytest.fixture
def mock_os_path_exists():
    original_exists = os.path.exists
    def side_effect(path):
        if 'credentials.json' in path:
            return False
        return original_exists(path)
    with patch('os.path.exists', side_effect=side_effect):
        yield

@pytest.mark.asyncio
async def test_rotation_advances_on_503(mock_mcp, mock_pipeline_deps, mock_runner, mock_os_path_exists, capsys):
    os.environ["GEMINI_API_KEYS"] = "key1, key2"

    runner1 = MagicMock()
    
    async def failing_run_async(*args, **kwargs):
        raise Exception("503 UNAVAILABLE")
        yield # to make it a generator
        
    async def passing_run_async(*args, **kwargs):
        class DummyEvent:
            tool_call = True
        yield DummyEvent()
        
    runner1.run_async = failing_run_async
    runner2 = MagicMock()
    runner2.run_async = passing_run_async
    
    mock_runner.side_effect = [runner1, runner2]
    
    await run_pipeline("dummy.mp4")
    
    assert mock_runner.call_count == 2
    captured = capsys.readouterr()
    assert "agent run: service unavailable on key 1 of 2, retrying on next key" in captured.out
    assert os.environ["GEMINI_API_KEY"] == "key2"

@pytest.mark.asyncio
async def test_rotation_advances_on_429(mock_mcp, mock_pipeline_deps, mock_runner, mock_os_path_exists, capsys):
    os.environ["GEMINI_API_KEYS"] = "key1, key2, key3"

    runner1 = MagicMock()
    
    async def failing_run_async(*args, **kwargs):
        class APIError(Exception):
            def __init__(self):
                self.code = 429
            def __str__(self):
                return "Quota Exceeded"
        raise APIError()
        yield
        
    runner2 = MagicMock()
    async def failing_run_async_status_code(*args, **kwargs):
        class APIError2(Exception):
            def __init__(self):
                self.status_code = 503
            def __str__(self):
                return "Unavailable"
        raise APIError2()
        yield
    runner2.run_async = failing_run_async_status_code

    async def passing_run_async(*args, **kwargs):
        yield MagicMock()
        
    runner1.run_async = failing_run_async
    runner3 = MagicMock()
    runner3.run_async = passing_run_async
    
    mock_runner.side_effect = [runner1, runner2, runner3]
    
    await run_pipeline("dummy.mp4")
    
    assert mock_runner.call_count == 3
    captured = capsys.readouterr()
    assert "agent run: service unavailable on key 1 of 3, retrying on next key" in captured.out
    assert "agent run: service unavailable on key 2 of 3, retrying on next key" in captured.out
    assert os.environ["GEMINI_API_KEY"] == "key3"

@pytest.mark.asyncio
async def test_non_retryable_error_does_not_rotate(mock_mcp, mock_pipeline_deps, mock_runner, mock_os_path_exists, capsys):
    os.environ["GEMINI_API_KEYS"] = "key1, key2"

    runner1 = MagicMock()
    
    async def failing_run_async(*args, **kwargs):
        raise ValueError("Some other error")
        yield
        
    runner1.run_async = failing_run_async
    mock_runner.side_effect = [runner1]
    
    with pytest.raises(ValueError, match="Some other error"):
        await run_pipeline("dummy.mp4")
        
    assert mock_runner.call_count == 1
    assert os.environ["GEMINI_API_KEY"] == "key1"

@pytest.mark.asyncio
async def test_all_keys_exhausted_raises(mock_mcp, mock_pipeline_deps, mock_runner, mock_os_path_exists, capsys):
    os.environ["GEMINI_API_KEYS"] = "key1, key2"

    runner1 = MagicMock()
    
    async def failing_run_async(*args, **kwargs):
        raise Exception("503 UNAVAILABLE")
        yield
        
    runner1.run_async = failing_run_async
    runner2 = MagicMock()
    runner2.run_async = failing_run_async
    
    mock_runner.side_effect = [runner1, runner2]
    
    with pytest.raises(Exception, match="503 UNAVAILABLE"):
        await run_pipeline("dummy.mp4")
        
    assert mock_runner.call_count == 2
    captured = capsys.readouterr()
    assert "agent run: service unavailable on key 1 of 2, retrying on next key" in captured.out
    assert "agent run: service unavailable on key 2 of 2, retrying on next key" in captured.out

@pytest.mark.asyncio
async def test_gemini_api_keys_unset_behaves_like_single_key(mock_mcp, mock_pipeline_deps, mock_runner, mock_os_path_exists, capsys):
    os.environ["GEMINI_API_KEY"] = "single_key"

    runner1 = MagicMock()
    
    async def failing_run_async(*args, **kwargs):
        raise Exception("RESOURCE_EXHAUSTED")
        yield
        
    runner1.run_async = failing_run_async
    mock_runner.side_effect = [runner1]
    
    with pytest.raises(Exception, match="RESOURCE_EXHAUSTED"):
        await run_pipeline("dummy.mp4")
        
    assert mock_runner.call_count == 1
    assert os.environ["GEMINI_API_KEY"] == "single_key"

@pytest.mark.asyncio
async def test_no_keys_raises_runtime_error(mock_mcp, mock_pipeline_deps, mock_runner, mock_os_path_exists):
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY environment variable is missing and fallback ~/.config/gemini/credentials.json not found"):
        await run_pipeline("dummy.mp4")

@pytest.mark.asyncio
async def test_rotation_does_not_double_certify(mock_mcp, mock_pipeline_deps, mock_runner, mock_os_path_exists, capsys):
    os.environ["GEMINI_API_KEYS"] = "key1, key2"

    runner1 = MagicMock()
    runner2 = MagicMock()
    
    async def run1(*args, **kwargs):
        agent = mock_runner.call_args.kwargs['agent']
        certify_tool = next(t for t in agent.tools if t.name == 'certify')
        
        with patch('flashframe.certify.write_certificate', new_callable=AsyncMock) as mock_write:
            mock_write.return_value = {"success": True}
            res = await certify_tool.func("scan_123", True, 0, 10, "passed", "none", 0.0)
            assert res == {"success": True}
        
        raise Exception("503 UNAVAILABLE")
        yield
        
    async def run2(*args, **kwargs):
        agent = mock_runner.call_args.kwargs['agent']
        certify_tool = next(t for t in agent.tools if t.name == 'certify')
        
        with patch('flashframe.certify.write_certificate', new_callable=AsyncMock) as mock_write:
            res = await certify_tool.func("scan_123", True, 0, 10, "passed", "none", 0.0)
            assert res == {"success": True}
            assert mock_write.call_count == 0
            
        class DummyEvent:
            tool_call = True
        yield DummyEvent()

    runner1.run_async = run1
    runner2.run_async = run2
    
    mock_runner.side_effect = [runner1, runner2]
    
    await run_pipeline("dummy.mp4")
    
    captured = capsys.readouterr()
    assert "certify() already completed in a previous attempt, skipping duplicate insert." in captured.out

@pytest.mark.asyncio
async def test_on_stage_callbacks_called(mock_mcp, mock_pipeline_deps, mock_runner, mock_os_path_exists):
    os.environ["GEMINI_API_KEY"] = "single_key"
    
    runner = MagicMock()
    
    async def passing_run_async(*args, **kwargs):
        agent = mock_runner.call_args.kwargs['agent']
        adjudicate_tool = next(t for t in agent.tools if t.name == 'final_adjudicate')
        certify_tool = next(t for t in agent.tools if t.name == 'certify')
        
        with patch('flashframe.adjudicate.run_adjudicate') as mock_adj, patch('flashframe.certify.write_certificate', new_callable=AsyncMock) as mock_cert:
            mock_adj_result = MagicMock()
            mock_adj_result.passed = True
            mock_adj_result.cause = "none"
            mock_adj_result.remediation = "none"
            mock_adj_result.measured_value = 0.0
            mock_adj_result.frame_start = 0
            mock_adj_result.frame_end = 10
            mock_adj.return_value = mock_adj_result
            
            mock_cert.return_value = {"success": True}
            
            adjudicate_tool.func(0, 10)
            await certify_tool.func("scan_123", True, 0, 10, "passed", "none", 0.0)
            
        class DummyEvent:
            tool_call = True
        yield DummyEvent()
        
    runner.run_async = passing_run_async
    mock_runner.return_value = runner
    
    called_stages = []
    def on_stage(stage):
        called_stages.append(stage)
        
    await run_pipeline("dummy.mp4", on_stage=on_stage)
    
    assert called_stages == ["Extract", "Ingest", "Detect", "Adjudicate", "Certify"]

@pytest.mark.asyncio
async def test_on_stage_early_return(mock_mcp, mock_runner, mock_os_path_exists):
    os.environ["GEMINI_API_KEY"] = "single_key"
    
    called_stages = []
    def on_stage(stage):
        called_stages.append(stage)
        
    with patch('flashframe.cli.run_extraction', return_value="scan_123"), \
         patch('flashframe.cli.setup_db_and_ingest', new_callable=AsyncMock), \
         patch('flashframe.cli.detect_violations', new_callable=AsyncMock, return_value='[]'):
         
        await run_pipeline("dummy.mp4", on_stage=on_stage)
        
    assert called_stages == ["Extract", "Ingest", "Detect"]
