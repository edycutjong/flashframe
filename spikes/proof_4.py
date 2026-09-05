import os
import sys
import asyncio
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
        SELECT frame_idx, pts_time, red_ratio,
               (dir != lagInFrame(dir) OVER (ORDER BY frame_idx)) AND (abs(d_luma) >= t_min_delta) AS is_flash,
               red_ratio > t_red_thresh AS is_red
        FROM framewise
    ),
    sliding AS (
        SELECT frame_idx, pts_time, red_ratio, is_flash, is_red,
               sum(is_flash) OVER (ORDER BY frame_idx ROWS BETWEEN 24 PRECEDING AND CURRENT ROW) AS window_flashes,
               max(is_red) OVER (ORDER BY frame_idx ROWS BETWEEN 24 PRECEDING AND CURRENT ROW) AS window_red
        FROM transitions
    ),
    violating_windows AS (
        SELECT frame_idx AS window_end_idx
        FROM sliding
        WHERE window_flashes > t_max_flashes OR window_red = 1
    ),
    violating_flashes AS (
        SELECT t.frame_idx, t.pts_time, t.red_ratio
        FROM transitions t
        JOIN violating_windows vw ON t.frame_idx BETWEEN vw.window_end_idx - 24 AND vw.window_end_idx
        WHERE t.is_flash = 1
    ),
    merged_spans AS (
        SELECT min(frame_idx) AS frame_start, max(frame_idx) AS frame_end,
               (count(DISTINCT frame_idx) / 2.0) / (greatest((max(frame_idx) - min(frame_idx)) / 25.0, 1.0/25.0)) AS measured_rate,
               max(red_ratio) AS peak_red
        FROM violating_flashes
    )
SELECT frame_start, frame_end, measured_rate AS flashes, peak_red
FROM merged_spans
"""
    res = await run_sql(run_query, detect_sql)
    print("\nFlagged spans:")
    print(res)

    # Write detected violations into ledger
    truncate_sql = "TRUNCATE TABLE violation_ledger"
    await run_sql(run_query, truncate_sql)

    write_ledger_sql = """
INSERT INTO violation_ledger
WITH
    current_scan AS (SELECT max(scan_id) FROM frame_metrics),
    threshold_values AS (
        SELECT
            min_luma_delta,
            max_flashes_sec
        FROM threshold_reference
        WHERE territory = 'UK-Ofcom' AND criterion = 'flash_rate' LIMIT 1
    ),
    framewise AS (
        SELECT frame_idx, pts_time, yavg, red_ratio,
               yavg - lagInFrame(yavg) OVER (ORDER BY frame_idx)              AS d_luma,
               sign(yavg - lagInFrame(yavg) OVER (ORDER BY frame_idx))        AS dir
        FROM frame_metrics WHERE scan_id = (SELECT * FROM current_scan) AND tile = 0
    ),
    transitions AS (
        SELECT frame_idx, pts_time, red_ratio,
               (dir != lagInFrame(dir) OVER (ORDER BY frame_idx)) AND (abs(d_luma) >= (SELECT min_luma_delta FROM threshold_values)) AS is_flash,
               red_ratio > 0.8 AS is_red
        FROM framewise
    ),
    sliding AS (
        SELECT frame_idx, pts_time, red_ratio, is_flash, is_red,
               sum(is_flash) OVER (ORDER BY frame_idx ROWS BETWEEN 24 PRECEDING AND CURRENT ROW) AS window_flashes,
               max(is_red) OVER (ORDER BY frame_idx ROWS BETWEEN 24 PRECEDING AND CURRENT ROW) AS window_red
        FROM transitions
    ),
    violating_windows AS (
        SELECT frame_idx AS window_end_idx
        FROM sliding
        WHERE window_flashes > (SELECT max_flashes_sec FROM threshold_values) OR window_red = 1
    ),
    violating_flashes AS (
        SELECT t.frame_idx, t.pts_time, t.red_ratio
        FROM transitions t
        JOIN violating_windows vw ON t.frame_idx BETWEEN vw.window_end_idx - 24 AND vw.window_end_idx
        WHERE t.is_flash = 1
    ),
    detected_violations AS (
        SELECT min(frame_idx) AS frame_start, max(frame_idx) AS frame_end,
               (count(DISTINCT frame_idx) / 2.0) / (greatest((max(frame_idx) - min(frame_idx)) / 25.0, 1.0/25.0)) AS measured,
               max(red_ratio) AS peak_red
        FROM violating_flashes
    )
SELECT
    (SELECT * FROM current_scan) AS scan_id,
    now() AS certified_at,
    'UK-Ofcom' AS territory,
    0 AS passed,
    dv.frame_start,
    dv.frame_end,
    dv.measured,
    (SELECT max_flashes_sec FROM threshold_values) AS threshold,
    NULL AS cause,
    NULL AS remediation
FROM detected_violations AS dv
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
