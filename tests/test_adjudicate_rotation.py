import pytest
import os
from unittest.mock import patch, MagicMock, mock_open

from flashframe.adjudicate import run_adjudicate, Verdict

@pytest.fixture(autouse=True)
def isolate_env():
    # Clean environment so we don't pick up real keys
    with patch.dict(os.environ, {}, clear=True):
        yield

@pytest.fixture
def mock_subprocess():
    with patch('subprocess.run') as m:
        yield m

@pytest.fixture
def mock_os_path_exists():
    original_exists = os.path.exists
    def side_effect(path):
        if 'credentials.json' in path:
            return False
        if path == 'span.mp4':
            return False
        return original_exists(path)
    with patch('os.path.exists', side_effect=side_effect):
        yield

@pytest.fixture
def mock_open_file():
    with patch('builtins.open', mock_open(read_data=b'dummy clip')):
        yield

@pytest.fixture
def mock_genai():
    with patch('flashframe.adjudicate.genai.Client') as m:
        yield m

def test_rotation_advances_on_429(mock_subprocess, mock_os_path_exists, mock_open_file, mock_genai, capsys):
    os.environ["GEMINI_API_KEYS"] = "key1, key2, key3"
    
    client1 = MagicMock()
    exc429 = Exception("RESOURCE_EXHAUSTED")
    client1.models.generate_content.side_effect = exc429
    
    client2 = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = '{"passed": true, "frame_start": 0, "frame_end": 10, "measured_value": 0.0, "threshold_value": 1.0, "cause": "none", "remediation": "none"}'
    client2.models.generate_content.return_value = mock_resp
    
    mock_genai.side_effect = [client1, client2]
    
    verdict = run_adjudicate("dummy.mp4", 0, 10)
    
    assert verdict.passed is True
    assert mock_genai.call_count == 2
    mock_genai.assert_any_call(api_key="key1")
    mock_genai.assert_any_call(api_key="key2")
    
    captured = capsys.readouterr()
    assert "quota exhausted on key 1 of 3, trying next" in captured.out

def test_non_quota_exception_propagates(mock_subprocess, mock_os_path_exists, mock_open_file, mock_genai, capsys):
    os.environ["GEMINI_API_KEYS"] = "key1, key2"
    
    client1 = MagicMock()
    exc_other = ValueError("Some other error")
    client1.models.generate_content.side_effect = exc_other
    mock_genai.return_value = client1
    
    with pytest.raises(ValueError, match="Some other error"):
        run_adjudicate("dummy.mp4", 0, 10)
        
    assert mock_genai.call_count == 1
    
    captured = capsys.readouterr()
    assert "quota exhausted" not in captured.out

def test_all_keys_exhausted_raises(mock_subprocess, mock_os_path_exists, mock_open_file, mock_genai, capsys):
    os.environ["GEMINI_API_KEYS"] = "key1, key2"
    
    client = MagicMock()
    exc429 = Exception("RESOURCE_EXHAUSTED")
    client.models.generate_content.side_effect = exc429
    mock_genai.return_value = client
    
    with pytest.raises(Exception, match="RESOURCE_EXHAUSTED"):
        run_adjudicate("dummy.mp4", 0, 10)
        
    assert mock_genai.call_count == 2
    
    captured = capsys.readouterr()
    assert "quota exhausted on key 1 of 2, trying next" in captured.out
    assert "quota exhausted on key 2 of 2" not in captured.out

def test_gemini_api_keys_unset_behaves_like_single_key(mock_subprocess, mock_os_path_exists, mock_open_file, mock_genai):
    os.environ["GEMINI_API_KEY"] = "single_key"
    
    client = MagicMock()
    exc429 = Exception("RESOURCE_EXHAUSTED")
    client.models.generate_content.side_effect = exc429
    mock_genai.return_value = client
    
    with pytest.raises(Exception, match="RESOURCE_EXHAUSTED"):
        run_adjudicate("dummy.mp4", 0, 10)
        
    assert mock_genai.call_count == 1
    mock_genai.assert_called_with(api_key="single_key")

def test_no_keys_raises_runtime_error(mock_subprocess, mock_os_path_exists, mock_open_file, mock_genai):
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY environment variable is missing and fallback ~/.config/gemini/credentials.json not found"):
        run_adjudicate("dummy.mp4", 0, 10)
