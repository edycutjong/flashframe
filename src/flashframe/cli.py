import click
import os
import sys
import asyncio
import json
from dotenv import load_dotenv

from .extract import run_extraction
from .ingest import setup_db_and_ingest
from .detect import detect_violations

from mcp.client.stdio import StdioServerParameters
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google.adk import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.genai import types
from google import genai

@click.group()
def cli():
    pass

@cli.command()
@click.option('--feature', is_flag=True)
def seed(feature):
    import subprocess
    if feature:
        # Generate feature clip
        pass
    else:
        click.echo("Running seed clip generation...")
        script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "generate_seed_clips.sh")
        subprocess.run(["bash", script_path], check=True)
        click.echo("Clips generated.")

@cli.command()
@click.argument('video_path')
def pipeline(video_path):
    asyncio.run(run_pipeline(video_path))

async def run_pipeline(video_path):
    load_dotenv(os.path.expanduser('~/.config/flashframe/clickhouse.env'))
    env = os.environ.copy()
    env.update({
        "CLICKHOUSE_HOST": os.environ["CLICKHOUSE_HOST"],
        "CLICKHOUSE_USER": os.environ["CLICKHOUSE_USER"],
        "CLICKHOUSE_PASSWORD": os.environ["CLICKHOUSE_PASSWORD"],
        "CLICKHOUSE_DATABASE": os.environ.get("CLICKHOUSE_DATABASE", "flashframe"),
        "CLICKHOUSE_ALLOW_WRITE_ACCESS": "true",
        "CLICKHOUSE_ALLOW_DROP": "true",
        "CHDB_ENABLED": "true"
    })

    mcp_python = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".venv", "bin", "python3")

    clickhouse = McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=mcp_python,
                args=["-m", "mcp_clickhouse.main"],
                env=env,
            )
        )
    )
    tools = await clickhouse.get_tools()
    run_query_tool = next(t for t in tools if t.name == "run_query")
    
    with open(os.path.expanduser('~/.config/gemini/credentials.json'), 'r') as f:
        creds = json.load(f)
        api_key = creds['keys'][0]['key']
        model_name = creds.get('model', 'gemini-3.6-flash')
        
    os.environ["GEMINI_API_KEY"] = api_key
    
    print(f"Extracting {video_path} at 10fps...")
    scan_id = run_extraction(video_path, fps_override=10)
    
    print(f"Ingesting {scan_id}...")
    await setup_db_and_ingest(run_query_tool, scan_id)
    
    print("Detecting violations...")
    detect_res = await detect_violations(run_query_tool, scan_id, fps=10)
    
    spans = []
    try:
        data = json.loads(detect_res)
        if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict) and 'frame_start' in data[0]:
            spans = data
        elif isinstance(data, dict) and 'frame_start' in data:
            spans = [data]
        elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
            spans = [{'frame_start': row[0], 'frame_end': row[1], 'flashes': row[2], 'peak_red': row[3], 'tile': row[4]} for row in data]
    except Exception as e:
        pass
        
    if not spans:
        print("No violations detected. PASS.")
        return
        
    span = spans[0]
    frame_start = span['frame_start']
    frame_end = span['frame_end']
    flashes = span['flashes']
    
    print(f"Flagged span {frame_start}-{frame_end} ({flashes} flashes/sec)...")
    
    resample_count = 0
    
    async def resample_frames(frame_start: int, frame_end: int, target_fps: int) -> dict:
        nonlocal resample_count
        if resample_count >= 2:
            return {"status": "error", "message": "Max resample iterations reached."}
        resample_count += 1
        print(f"\n>>> resample_frames(span, {target_fps}) <<<\n")
        scan_id_new = run_extraction(video_path, fps_override=target_fps, frame_start=frame_start, frame_end=frame_end)
        await setup_db_and_ingest(run_query_tool, scan_id_new)
        new_res = await detect_violations(run_query_tool, scan_id_new, fps=target_fps)
        return {"status": "success", "detection_result": new_res, "new_scan_id": scan_id_new}

    def final_adjudicate(frame_start: int, frame_end: int) -> dict:
        from .adjudicate import run_adjudicate
        print(f"\n>>> adjudicate({frame_start}, {frame_end}) <<<\n")
        
        # Implement backoff logic
        import time
        from google.genai.errors import APIError
        for attempt in range(5):
            try:
                v = run_adjudicate(video_path, frame_start, frame_end, model=model_name, api_key=api_key)
                return {"passed": v.passed, "cause": v.cause, "remediation": v.remediation, "measured_value": v.measured_value, "threshold_value": v.threshold_value, "frame_start": v.frame_start, "frame_end": v.frame_end}
            except APIError as e:
                if e.code in [429, 503]:
                    wait_time = 20 * (attempt + 1)
                    print(f"Rate limited ({e.code}), sleeping for {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    raise
        return {"error": "Exceeded maximum retries for Gemini API"}
        
    async def certify(scan_id: str, passed: bool, frame_start: int, frame_end: int, measured_value: float, threshold_value: float, cause: str, remediation: str) -> dict:
        print(f"\n>>> certify <<<\n")
        from .adjudicate import Verdict
        from .certify import write_certificate
        v = Verdict(passed=passed, frame_start=frame_start, frame_end=frame_end, measured_value=measured_value, threshold_value=threshold_value, cause=cause, remediation=remediation)
        cert = await write_certificate(run_query_tool, scan_id, v)
        print("Final Certificate Generated.")
        return cert

    agent = LlmAgent(
        model=model_name,
        name="flashframe_adjudicator",
        instruction="""You are the Flashframe Adjudicator orchestrating a photosensitive epilepsy compliance pipeline.
You are given a flagged span.
If the current scan is from a 10fps extraction (which you should assume unless told otherwise), you MUST call resample_frames(frame_start, frame_end, target_fps=30) to get a more accurate reading.
If the 30fps scan is still flagged or borderline, you MUST call resample_frames(frame_start, frame_end, target_fps=60). Max 2 resamples.
Once you have the 60fps result (or 30fps if it definitively passes), you MUST call final_adjudicate(frame_start, frame_end) to get Gemini's blind visual verdict on the video.
Finally, call certify(scan_id, passed, frame_start, frame_end, measured_value, threshold_value, cause, remediation) with Gemini's verdict to write the violation ledger and produce a certificate.
""",
        tools=[FunctionTool(resample_frames), FunctionTool(final_adjudicate), FunctionTool(certify)],
    )
    
    prompt = types.Content(role="user", parts=[types.Part.from_text(text=f"""Please adjudicate this flagged span.
scan_id: {scan_id}
frame_start: {frame_start}
frame_end: {frame_end}
""")])

    session_service = InMemorySessionService()
    runner = Runner(agent=agent, app_name="flashframe", session_service=session_service, auto_create_session=True)
    
    async for event in runner.run_async(user_id="user1", session_id="session1", new_message=prompt):
        if hasattr(event, "tool_call"):
            pass

if __name__ == "__main__": cli()
