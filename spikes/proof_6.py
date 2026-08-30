import os
import sys
import asyncio
import json
from dotenv import load_dotenv
from mcp.client.stdio import StdioServerParameters
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google.adk import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.genai import types

def resample_frames(scan_id: str, frame_start: int, frame_end: int, target_fps: int) -> dict:
    """Re-extract photometrics for a span at higher fps, re-insert, re-query."""
    print(f"\n>>> FUNCTION CALL: resample_frames(scan_id={scan_id}, {frame_start}-{frame_end}, {target_fps}fps) <<<\n")
    return {
        "status": "success",
        "new_measured_value": 2.5,
        "new_threshold": 3.0,
        "message": "Re-sampled at 60fps. Rate resolved unambiguously to 2.5 flashes/sec (PASS)."
    }

async def proof_6():
    load_dotenv(os.path.expanduser('~/.config/flashframe/clickhouse.env'))
    
    env = os.environ.copy()
    env.update({
        "CLICKHOUSE_HOST": os.environ["CLICKHOUSE_HOST"],
        "CLICKHOUSE_USER": os.environ["CLICKHOUSE_USER"],
        "CLICKHOUSE_PASSWORD": os.environ["CLICKHOUSE_PASSWORD"],
        "CLICKHOUSE_DATABASE": os.environ.get("CLICKHOUSE_DATABASE", "flashframe"),
        "CLICKHOUSE_ALLOW_WRITE_ACCESS": "true",
        "CHDB_ENABLED": "true"
    })

    clickhouse = McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=sys.executable,
                args=["-m", "mcp_clickhouse.main"],
                env=env,
            )
        )
    )
    
    with open(os.path.expanduser('~/.config/gemini/credentials.json'), 'r') as f:
        creds = json.load(f)
        api_key = creds['keys'][0]['key']
        model_name = creds.get('model', 'gemini-2.5-pro')
        
    os.environ["GEMINI_API_KEY"] = api_key
    
    print("Proof 6: ADK Loop Decisions")
    tools = await clickhouse.get_tools()
    
    agent = LlmAgent(
        model=model_name,
        name="flashframe_adjudicator",
        instruction="""You are the Flashframe Adjudicator. 
Your job is to evaluate flagged video spans for photosensitive epilepsy violations.
If a span is BORDERLINE (within 10% of the threshold), you MUST call resample_frames(scan_id, frame_start, frame_end, 60) to get a more accurate reading at 60fps before finalizing your verdict.
Do not provide a text response until you have called the function on borderline inputs.
Once you have the final results, output a clear PASS or FAIL verdict.
""",
        tools=[*tools, FunctionTool(resample_frames)],
    )

    prompt = types.Content(role="user", parts=[types.Part.from_text(text="""Please adjudicate this span.
scan_id: 0e403ae2-2ca4-45be-8270-5b2cddc5d9e7
frame_start: 1000
frame_end: 1074
measured_value: 2.9 flashes/sec
threshold_value: 3.0 flashes/sec
This is borderline (2.9 is very close to 3.0).""")])

    print("Driving agent with borderline input...")
    session_service = InMemorySessionService()
    runner = Runner(agent=agent, app_name="flashframe", session_service=session_service, auto_create_session=True)
    
    async for event in runner.run_async(user_id="user1", session_id="session1", new_message=prompt):
        print(f"Event received: {type(event).__name__}")
        # Just to show something
        if hasattr(event, "tool_call"):
            print(f"Tool call: {getattr(event, 'tool_call', None)}")

if __name__ == "__main__":
    asyncio.run(proof_6())
