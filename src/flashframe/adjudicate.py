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
    if not api_key:
        api_key = os.environ.get("GEMINI_API_KEY")
        env_model = os.environ.get("GEMINI_MODEL")
        if env_model:
            model = env_model
            
        if not api_key:
            cred_path = os.path.expanduser('~/.config/gemini/credentials.json')
            if os.path.exists(cred_path):
                with open(cred_path, 'r') as f:
                    creds = json.load(f)
                    api_key = creds['keys'][1]['key']
                    model = creds.get('model', model)
            else:
                raise RuntimeError("GEMINI_API_KEY environment variable is missing and fallback ~/.config/gemini/credentials.json not found")
            
    client = genai.Client(api_key=api_key)
    
    if os.path.exists('span.mp4'): os.remove('span.mp4')
    ss = frame_start / 25.0
    t = (frame_end - frame_start + 1) / 25.0
    subprocess.run(['ffmpeg', '-ss', str(ss), '-t', str(t), '-i', video_path, '-c:v', 'libx264', 'span.mp4', '-y'], check=True, stderr=subprocess.DEVNULL)
    
    with open('span.mp4', 'rb') as f:
        clip = f.read()
    
    resp = client.models.generate_content(
        model=model,
        contents=[
            types.Part(
                inline_data=types.Blob(data=clip, mime_type="video/mp4"),
                video_metadata=types.VideoMetadata(fps=24)
            ),
            types.Part.from_text(text=f"The frame span from frames {frame_start} to {frame_end} in the source video corresponds to this short clip. The automated luminance scan flagged this span for potential photosensitivity hazards. Please analyze the visual content to determine if there are harmful flashes on screen, if they pass or fail the limit, and what on-screen content causes them.")
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=Verdict,
            temperature=0.0
        ),
    )
    
    return Verdict.model_validate_json(resp.text)
