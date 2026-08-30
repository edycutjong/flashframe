import json

async def run_sql(tool, sql):
    res = await tool.run_async(args={"query": sql}, tool_context=None)
    if getattr(res, "isError", False) or (isinstance(res, dict) and res.get("isError")):
        raise Exception(f"SQL Error: {res}")
    return res

async def detect_violations(run_query_tool, scan_id, fps=10):
    detect_sql = f"""
WITH
    '{scan_id}' AS current_scan,
    (SELECT min_luma_delta FROM threshold_reference WHERE territory = 'UK-Ofcom' AND criterion = 'flash_rate' LIMIT 1) AS t_min_delta,
    (SELECT max_flashes_sec FROM threshold_reference WHERE territory = 'UK-Ofcom' AND criterion = 'flash_rate' LIMIT 1) AS t_max_flashes,
    (SELECT 0.8) AS t_red_thresh,
    framewise AS (
        SELECT frame_idx, pts_time, tile, yavg, red_ratio,
               yavg - lagInFrame(yavg) OVER (PARTITION BY tile ORDER BY frame_idx)              AS d_luma,
               sign(yavg - lagInFrame(yavg) OVER (PARTITION BY tile ORDER BY frame_idx))        AS dir
        FROM frame_metrics WHERE scan_id = current_scan
    ),
    transitions AS (
        SELECT frame_idx, pts_time, tile, red_ratio,
               (dir != lagInFrame(dir) OVER (PARTITION BY tile ORDER BY frame_idx)) AND (abs(d_luma) >= t_min_delta) AS is_flash,
               red_ratio > t_red_thresh AS is_red
        FROM framewise
    ),
    sliding AS (
        SELECT frame_idx, pts_time, tile, red_ratio, is_flash, is_red,
               sum(is_flash) OVER (PARTITION BY tile ORDER BY frame_idx ROWS BETWEEN {int(fps)-1} PRECEDING AND CURRENT ROW) AS window_flashes,
               max(is_red) OVER (PARTITION BY tile ORDER BY frame_idx ROWS BETWEEN {int(fps)-1} PRECEDING AND CURRENT ROW) AS window_red
        FROM transitions
    ),
    violating_windows AS (
        SELECT frame_idx AS window_end_idx, tile
        FROM sliding
        WHERE window_flashes > t_max_flashes OR window_red = 1
    ),
    violating_flashes AS (
        SELECT t.frame_idx, t.pts_time, t.tile, t.red_ratio
        FROM transitions t
        JOIN violating_windows vw ON t.tile = vw.tile AND t.frame_idx BETWEEN vw.window_end_idx - 24 AND vw.window_end_idx
        WHERE t.is_flash = 1
    ),
    merged_spans AS (
        SELECT min(frame_idx) AS frame_start, max(frame_idx) AS frame_end, tile,
               (count(DISTINCT frame_idx) / 2.0) / (greatest((max(frame_idx) - min(frame_idx)) / 25.0, 1.0/25.0)) AS measured_rate,
               max(red_ratio) AS peak_red
        FROM violating_flashes
        GROUP BY tile
    )
SELECT frame_start, frame_end, measured_rate AS flashes, peak_red, tile
FROM merged_spans
ORDER BY flashes DESC
"""
    res = await run_sql(run_query_tool, detect_sql)
    
    try:
        if hasattr(res, "content") and len(res.content) > 0 and hasattr(res.content[0], "text"):
            text = res.content[0].text
        elif hasattr(res, "text"):
            text = res.text
        elif isinstance(res, list) and len(res) > 0 and hasattr(res[0], "text"):
            text = res[0].text
        elif isinstance(res, dict) and "content" in res:
            text = res["content"][0]["text"]
        else:
            text = str(res)
            
        data = json.loads(text)
        if "rows" in data:
            return json.dumps(data["rows"])
        return json.dumps(data)
    except Exception as e:
        print("Failed to parse detect_sql response:", res, e)
        return "[]"
