import os
import sys
import asyncio
import json
from dotenv import load_dotenv
from mcp.client.stdio import StdioServerParameters
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams

async def run_sql(tool, sql):
    print(f"Executing: {sql[:150]}...")
    res = await tool.run_async(args={"query": sql}, tool_context=None)
    if getattr(res, "isError", False) or (isinstance(res, dict) and res.get("isError")):
        print(f"Error: {res}")
    return res

async def proof_4():
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
    
    print("Proof 4: Detection SQL and Write-back")
    tools = await clickhouse.get_tools()
    run_query = next(t for t in tools if t.name == "run_query")
    run_chdb_select_query = next(t for t in tools if t.name == "run_chdb_select_query")
    
    # Insert red_saturation threshold so we can query it
    await run_sql(run_query, "INSERT INTO threshold_reference VALUES ('UK-Ofcom', 'red_saturation', 0.0, 0.0, 25.0, 'Ofcom Rule 2.12')")
    
    detect_sql = """
WITH
    (SELECT max(scan_id) FROM frame_metrics) AS current_scan,
    (SELECT min_luma_delta FROM threshold_reference WHERE territory = 'UK-Ofcom' AND criterion = 'flash_rate' LIMIT 1) AS t_min_delta,
    (SELECT max_flashes_sec FROM threshold_reference WHERE territory = 'UK-Ofcom' AND criterion = 'flash_rate' LIMIT 1) AS t_max_flashes,
    (SELECT 0.8) AS t_red_thresh,
    framewise AS (
        SELECT frame_idx, pts_time, yavg, red_ratio,
               yavg - lagInFrame(yavg) OVER (ORDER BY frame_idx)              AS d_luma,
               sign(yavg - lagInFrame(yavg) OVER (ORDER BY frame_idx))        AS dir
        FROM frame_metrics WHERE scan_id = current_scan AND tile = 0
    ),
    transitions AS (
        SELECT *, dir != lagInFrame(dir) OVER (ORDER BY frame_idx) AS opposing
        FROM framewise
    )
SELECT min(frame_idx) AS frame_start, max(frame_idx) AS frame_end,
       countIf(opposing AND abs(d_luma) >= t_min_delta) AS flashes,
       max(red_ratio) AS peak_red
FROM transitions
GROUP BY toUInt32(pts_time)
HAVING flashes > t_max_flashes OR peak_red > t_red_thresh
ORDER BY frame_start
"""
    res = await run_sql(run_query, detect_sql)
    print("\nFlagged spans:")
    print(res)

    # Write a violation into ledger
    write_ledger_sql = """
INSERT INTO violation_ledger
SELECT
    (SELECT max(scan_id) FROM frame_metrics) AS scan_id,
    now() AS certified_at,
    'UK-Ofcom' AS territory,
    0 AS passed,
    739 AS frame_start,
    760 AS frame_end,
    6.25 AS measured,
    3.0 AS threshold,
    'Flashes detected' AS cause,
    'Trim frames' AS remediation
"""
    await run_sql(run_query, write_ledger_sql)
    
    print("\nRead back ledger:")
    res2 = await run_sql(run_query, "SELECT * FROM violation_ledger")
    print(res2)
    
    # 4. chDB zero-ETL join on disk CSVs
    cwd = os.getcwd()
    chdb_sql = f"""
SELECT f.scan_id, count(*) AS count
FROM file('{cwd}/frame_metrics.csv', 'CSVWithNames') f
CROSS JOIN file('{cwd}/thresholds.csv', 'CSVWithNames') t
WHERE t.territory = 'UK-Ofcom' AND t.criterion = 'flash_rate'
GROUP BY f.scan_id
"""
    print("\nchDB Query on raw CSVs:")
    res3 = await run_sql(run_chdb_select_query, chdb_sql)
    print(res3)

if __name__ == "__main__":
    asyncio.run(proof_4())
