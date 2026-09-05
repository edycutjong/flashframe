from pydantic import BaseModel
import subprocess
import os
import json
from google import genai
from google.genai import types

class Verdict(BaseModel):
    passed: bool
    frame_start: int
    frame_end: int
    measured_value: float
    threshold_value: float
    cause: str
    remediation: str

def run_adjudicate(video_path, frame_start, frame_end, model="gemini-3.6-flash", api_key=None):
    candidate_keys = []
    
    if api_key:
        candidate_keys = [api_key]
    else:
        env_model = os.environ.get("GEMINI_MODEL")
        if env_model:
            model = env_model
            
        if "GEMINI_API_KEYS" in os.environ:
            keys_str = os.environ.get("GEMINI_API_KEYS", "")
            candidate_keys = [k.strip() for k in keys_str.split(',') if k.strip()]
        else:
            env_api_key = os.environ.get("GEMINI_API_KEY")
            if env_api_key:
                candidate_keys = [env_api_key]
            else:
                cred_path = os.path.expanduser('~/.config/gemini/credentials.json')
                if os.path.exists(cred_path):
                    with open(cred_path, 'r') as f:
                        creds = json.load(f)
                        candidate_keys = [creds['keys'][1]['key']]
                        model = creds.get('model', model)
                else:
                    raise RuntimeError("GEMINI_API_KEY environment variable is missing and fallback ~/.config/gemini/credentials.json not found")

    if not candidate_keys:
        raise RuntimeError("No API keys available")
    if os.path.exists('span.mp4'):
        os.remove('span.mp4')
    ss = frame_start / 25.0
    t = (frame_end - frame_start + 1) / 25.0
    subprocess.run(['ffmpeg', '-ss', str(ss), '-t', str(t), '-i', video_path, '-c:v', 'libx264', 'span.mp4', '-y'], check=True, stderr=subprocess.DEVNULL)
    
    with open('span.mp4', 'rb') as f:
        clip = f.read()
    
    last_exc = None
    for i, current_key in enumerate(candidate_keys):
        client = genai.Client(api_key=current_key)
        try:
            resp = client.models.generate_content(
                model=model,
                contents=[
                    types.Part(
                        inline_data=types.Blob(data=clip, mime_type="video/mp4"),
                        video_metadata=types.VideoMetadata(fps=24)
                    ),
                    types.Part.from_text(
                        text=f"The frame span from frames {frame_start} to {frame_end} in the "
                             "source video corresponds to this short clip. The automated luminance "
                             "scan flagged this span for potential photosensitivity hazards. Please "
                             "analyze the visual content to determine if there are harmful flashes on "
                             "screen, if they pass or fail the limit, and what on-screen content "
                             "causes them."
                    )
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=Verdict,
                    temperature=0.0
                )
            )
            return Verdict.model_validate_json(resp.text)
        except Exception as e:
            is_quota_error = False
            is_unavailable_error = False
            err_str = str(e)
            
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                is_quota_error = True
            elif hasattr(e, 'code') and getattr(e, 'code') == 429:
                is_quota_error = True
            elif hasattr(e, 'status_code') and getattr(e, 'status_code') == 429:
                is_quota_error = True

            if "503" in err_str or "UNAVAILABLE" in err_str:
                is_unavailable_error = True
            elif hasattr(e, 'code') and getattr(e, 'code') == 503:
                is_unavailable_error = True
            elif hasattr(e, 'status_code') and getattr(e, 'status_code') == 503:
                is_unavailable_error = True

            is_retryable_error = is_quota_error or is_unavailable_error

            if not is_retryable_error:
                raise
            
            last_exc = e
            if i < len(candidate_keys) - 1:
                reason = "quota exhausted" if is_quota_error else "service unavailable"
                print(f"{reason} on key {i+1} of {len(candidate_keys)}, trying next")

    raise last_exc
