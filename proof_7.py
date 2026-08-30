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

def proof_7():
    with open(os.path.expanduser('~/.config/gemini/credentials.json'), 'r') as f:
        creds = json.load(f)
        api_key = creds['keys'][0]['key']
        model = creds.get('model', 'gemini-2.5-pro')

    client = genai.Client(api_key=api_key)
    
    # Case A: Confirm a real hazard blindly
    print("Proof 7 - Case A: Confirm a true positive")
    if os.path.exists('span.mp4'): os.remove('span.mp4')
    subprocess.run(['ffmpeg', '-ss', '29.0', '-t', '2.0', '-i', 'test_clip.mp4', '-c:v', 'libx264', 'span.mp4', '-y'], check=True, capture_output=True)
    with open('span.mp4', 'rb') as f:
        clip_a = f.read()
    
    resp_a = client.models.generate_content(
        model=model,
        contents=[
            types.Part(
                inline_data=types.Blob(data=clip_a, mime_type="video/mp4"),
                video_metadata=types.VideoMetadata(fps=24)
            ),
            types.Part.from_text(text="The frame span from frames 739 to 761 in the source video corresponds to this short clip. The automated luminance scan flagged this span for potential photosensitivity hazards due to high luminance variance. Please analyze the visual content to determine the actual flash rate, if there are harmful flashes on screen, if they pass or fail the Ofcom limit of 3 flashes/sec, and what on-screen content causes them.")
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=Verdict,
            temperature=0.0
        ),
    )
    verdict_a = Verdict.model_validate_json(resp_a.text)
    print("Case A Verdict:")
    print(verdict_a)
    
    # Case B: Clear a false positive blindly
    print("\nProof 7 - Case B: Clear a false positive")
    with open('synthetic_false_positive.mp4', 'rb') as f:
        clip_b = f.read()

    resp_b = client.models.generate_content(
        model=model,
        contents=[
            types.Part(
                inline_data=types.Blob(data=clip_b, mime_type="video/mp4"),
                video_metadata=types.VideoMetadata(fps=24)
            ),
            types.Part.from_text(text="The automated luminance scan flagged this span for potential photosensitivity hazards due to high luminance variance (flashes detected). Please analyze the visual content to determine if there are harmful flashes on screen, if they pass or fail the Ofcom limit of 3 flashes/sec, and what on-screen content causes them. Keep in mind that a flash must cover at least 25% of the screen area to be considered a hazard.")
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=Verdict,
            temperature=0.0
        ),
    )
    verdict_b = Verdict.model_validate_json(resp_b.text)
    print("Case B Verdict:")
    print(verdict_b)

if __name__ == "__main__":
    proof_7()
