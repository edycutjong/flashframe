import os
import json
import uuid
import time
import asyncio
from dotenv import load_dotenv

import src.flashframe.extract as extract
import src.flashframe.detect as detect
import src.flashframe.ingest as ingest

from mcp.client.stdio import StdioServerParameters
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams

async def run_bench():
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
    
    mcp_python = os.path.join(".venv", "bin", "python3")
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
    
    if not os.path.exists("bench_feature.mp4"):
        print("bench_feature.mp4 not found.")
        return

    print("Extracting bench_feature.mp4 outside timed region...")
    scan_id = extract.run_extraction("bench_feature.mp4", fps_override=25)
    
    with open('frame_metrics.csv', 'r') as f:
        rows = sum(1 for _ in f) - 1
    
    print("Warming up ClickHouse...")
    await run_query_tool.run_async(args={"query": "SELECT 1"}, tool_context=None)
    
    print("Running timed region 5 times...")
    times = []
    
    for i in range(5):
        start = time.time()
        await ingest.setup_db_and_ingest(run_query_tool, scan_id)
        res = await detect.detect_violations(run_query_tool, scan_id, fps=25)
        end = time.time()
        dur = end - start
        times.append(dur)
        print(f"Run {i+1}: {dur*1000:.2f} ms (EXCLUDING ffmpeg extraction and Gemini adjudication)")
    
    times.sort()
    p50 = times[2]
    p95 = times[4]
    
    print(f"\n--- BENCHMARK RESULTS ---")
    print(f"p50: {p50*1000:.2f} ms (EXCLUDING ffmpeg extraction and Gemini adjudication)")
    print(f"p95: {p95*1000:.2f} ms (EXCLUDING ffmpeg extraction and Gemini adjudication)")
    print(f"N: 5")
    print(f"Rows ingested: {rows}")
    
    with open("manifest.json") as f:
        manifest = json.load(f)
    
    spans = json.loads(res)
    span = spans[0] if spans else None
    
    if span:
        assert abs(span['frame_start'] - manifest['strobe_start_frame']) <= 50, f"Detected start {span['frame_start']} does not match manifest {manifest['strobe_start_frame']}"
        print(f"Assertion passed: Detected span start {span['frame_start']} matches manifest offset {manifest['strobe_start_frame']}")
    else:
        print("WARNING: No span detected!")
        
    print("\nRunning control_clean.mp4 benchmark to verify 0 false positives...")
    control_scan_id = extract.run_extraction("assets/control_clean.mp4", fps_override=25)
    await ingest.setup_db_and_ingest(run_query_tool, control_scan_id)
    control_res = await detect.detect_violations(run_query_tool, control_scan_id, fps=25)
    
    control_spans = json.loads(control_res)
    fp_count = len(control_spans)
    print(f"False positive count: {fp_count}")
    assert fp_count == 0, "False positive storm detected!"

if __name__ == "__main__":
    asyncio.run(run_bench())
