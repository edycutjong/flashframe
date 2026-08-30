import os
import csv

async def run_sql(tool, sql):
    res = await tool.run_async(args={"query": sql}, tool_context=None)
    if getattr(res, "isError", False) or (isinstance(res, dict) and res.get("isError")):
        print(f"Error executing SQL: {sql[:100]}... {res}")
    return res

async def setup_db_and_ingest(run_query_tool, scan_id):
    # Try to create tables if they don't exist, otherwise truncate them
    await run_sql(run_query_tool, """
CREATE TABLE IF NOT EXISTS frame_metrics (
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

    await run_sql(run_query_tool, """
CREATE TABLE IF NOT EXISTS threshold_reference (
    territory       LowCardinality(String),
    criterion       LowCardinality(String),
    max_flashes_sec Float32,
    min_luma_delta  Float32,
    screen_area_pct Float32,
    citation        String
) ENGINE = MergeTree ORDER BY (territory, criterion)
""")

    await run_sql(run_query_tool, """
CREATE TABLE IF NOT EXISTS violation_ledger (
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

    await run_sql(run_query_tool, f"DELETE FROM frame_metrics WHERE scan_id = '{scan_id}'")
    await run_sql(run_query_tool, "TRUNCATE TABLE IF EXISTS threshold_reference")

    # We get threshold from thresholds.csv in CWD
    thresholds_file = 'thresholds.csv'
    if not os.path.exists(thresholds_file):
        # Create a default if not exists
        with open(thresholds_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['territory', 'criterion', 'max_flashes_sec', 'min_luma_delta', 'screen_area_pct', 'citation'])
            writer.writerow(['UK-Ofcom', 'flash_rate', 3.0, 20.0, 25.0, 'Ofcom Rule 2.12'])
    
    with open(thresholds_file, 'r') as f:
        reader = csv.reader(f)
        next(reader) # skip header
        values = []
        for row in reader:
            values.append(f"('{row[0]}', '{row[1]}', {row[2]}, {row[3]}, {row[4]}, '{row[5]}')")
        
        if values:
            await run_sql(run_query_tool, f"INSERT INTO threshold_reference VALUES {','.join(values)}")
            
    with open('frame_metrics.csv', 'r') as f:
        reader = csv.reader(f)
        next(reader)
        values = []
        for row in reader:
            values.append(f"('{row[0]}', {row[1]}, {row[2]}, {row[3]}, {row[4]}, {row[5]}, {row[6]}, {row[7]}, {row[8]})")
            if len(values) >= 5000:
                await run_sql(run_query_tool, f"INSERT INTO frame_metrics VALUES {','.join(values)}")
                values = []
        if values:
            await run_sql(run_query_tool, f"INSERT INTO frame_metrics VALUES {','.join(values)}")
