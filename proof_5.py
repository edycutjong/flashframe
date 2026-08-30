import os
import subprocess
from pydantic import BaseModel
from google import genai
from google.genai import types
import json

class Verdict(BaseModel):
    passed: bool
    frame_start: int
    frame_end: int
    measured_value: float
    threshold_value: float
    cause: str
    remediation: str

def proof_5():
    # 1. cut clip
    if os.path.exists('span.mp4'): os.remove('span.mp4')
    subprocess.run(['ffmpeg', '-ss', '29.0', '-t', '2.0', '-i', 'test_clip.mp4', '-c:v', 'libx264', 'span.mp4', '-y'], check=True, capture_output=True)
    
    with open('span.mp4', 'rb') as f:
        clip_bytes = f.read()

    with open(os.path.expanduser('~/.config/gemini/credentials.json'), 'r') as f:
        creds = json.load(f)
        api_key = creds['keys'][0]['key']
        # use model specified or default
        model = creds.get('model', 'gemini-2.5-pro')

    client = genai.Client(api_key=api_key)
    
    print("Proof 5: Gemini Adjudicates")
    resp = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=clip_bytes, mime_type="video/mp4"),
            types.Part.from_text(text="Measured luminance curve shows a full-screen strobe of 6.25 flashes/sec between frames 739 and 760 (around 0.5s into this clip).\nPlease verify if there are harmful flashes on screen, if they pass or fail the Ofcom limit of 3 flashes/sec, and what on-screen content causes them.")
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=Verdict,
            temperature=0.0
        ),
    )
    
    print("Gemini response:")
    print(resp.text)
    
    verdict = Verdict.model_validate_json(resp.text)
    print("\nParsed verdict object:")
    print(verdict)

if __name__ == "__main__":
    proof_5()
