import os
import sys
import asyncio
import csv
from dotenv import load_dotenv
from mcp.client.stdio import StdioServerParameters
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams

async def run_sql(tool, sql):
    print(f"Executing: {sql[:100]}...")
    res = await tool.run_async(args={"query": sql}, tool_context=None)
    if getattr(res, "isError", False) or (isinstance(res, dict) and res.get("isError")):
        print(f"Error: {res}")
    return res

async def proof_3():
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

    clickhouse = McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=sys.executable,
                args=["-m", "mcp_clickhouse.main"],
                env=env,
            )
        )
    )
    
    print("Proof 3: Create and Ingest")
    tools = await clickhouse.get_tools()
    run_query = next(t for t in tools if t.name == "run_query")
    
    # 1. Create tables
    await run_sql(run_query, "DROP TABLE IF EXISTS frame_metrics")
    await run_sql(run_query, "DROP TABLE IF EXISTS threshold_reference")
    await run_sql(run_query, "DROP TABLE IF EXISTS violation_ledger")
    
    await run_sql(run_query, """
CREATE TABLE frame_metrics (
    scan_id      UUID,
    frame_idx    UInt32,
    pts_time     Float64,
    tile         UInt8,
    yavg         Float32,
    ymax         Float32,
    ymin         Float32,
    satavg       Float32,
    red_ratio    Float32
) ENGINE = MergeTree ORDER BY (scan_id, tile, frame_idx)
""")

    await run_sql(run_query, """
CREATE TABLE threshold_reference (
    territory       LowCardinality(String),
    criterion       LowCardinality(String),
    max_flashes_sec Float32,
    min_luma_delta  Float32,
    screen_area_pct Float32,
    citation        String
) ENGINE = MergeTree ORDER BY (territory, criterion)
""")

    await run_sql(run_query, """
CREATE TABLE violation_ledger (
    scan_id       UUID,
    certified_at  DateTime,
    territory     LowCardinality(String),
    passed        UInt8,
    frame_start   UInt32,
    frame_end     UInt32,
    measured      Float32,
    threshold     Float32,
    cause         String,
    remediation   String
) ENGINE = MergeTree ORDER BY (scan_id, certified_at)
""")

    # 2. Insert thresholds
    with open('thresholds.csv', 'r') as f:
        reader = csv.reader(f)
        next(reader) # skip header
        values = []
        for row in reader:
            values.append(f"('{row[0]}', '{row[1]}', {row[2]}, {row[3]}, {row[4]}, '{row[5]}')")
        
        await run_sql(run_query, f"INSERT INTO threshold_reference VALUES {','.join(values)}")
        
    # 3. Insert frame_metrics
    with open('frame_metrics.csv', 'r') as f:
        reader = csv.reader(f)
        next(reader)
        values = []
        for row in reader:
            values.append(f"('{row[0]}', {row[1]}, {row[2]}, {row[3]}, {row[4]}, {row[5]}, {row[6]}, {row[7]}, {row[8]})")
            
            if len(values) >= 5000:
                await run_sql(run_query, f"INSERT INTO frame_metrics VALUES {','.join(values)}")
                values = []
        if values:
            await run_sql(run_query, f"INSERT INTO frame_metrics VALUES {','.join(values)}")

    # 4. Verification queries
    print("\n--- Evidence ---")
    res1 = await run_query.run_async(args={"query": "SELECT count(*) FROM frame_metrics"}, tool_context=None)
    print("frame_metrics count:", res1)
    res2 = await run_query.run_async(args={"query": "SELECT count(*) FROM threshold_reference"}, tool_context=None)
    print("threshold_reference count:", res2)

if __name__ == "__main__":
    asyncio.run(proof_3())
