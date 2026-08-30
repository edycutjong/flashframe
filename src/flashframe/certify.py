import json

async def write_certificate(run_query_tool, scan_id, passed, frame_start, frame_end, measured_value, cause, remediation, gemini_estimated_rate=0.0):
    await run_query_tool.run_async(args={"query": "ALTER TABLE violation_ledger ADD COLUMN IF NOT EXISTS gemini_estimated_rate Float32"}, tool_context=None)

    write_ledger_sql = f"""
INSERT INTO violation_ledger (scan_id, certified_at, territory, passed, frame_start, frame_end, measured, threshold, cause, remediation, gemini_estimated_rate)
SELECT
    '{scan_id}' AS scan_id,
    now() AS certified_at,
    'UK-Ofcom' AS territory,
    {1 if passed else 0} AS passed,
    {frame_start},
    {frame_end},
    {measured_value},
    (SELECT max_flashes_sec FROM threshold_reference WHERE territory = 'UK-Ofcom' AND criterion = 'flash_rate' LIMIT 1),
    '{cause.replace("'", "''")}',
    '{remediation.replace("'", "''")}',
    {gemini_estimated_rate}
"""
    res = await run_query_tool.run_async(args={"query": write_ledger_sql}, tool_context=None)
    
    # read it back
    read_back_query = f"SELECT * FROM violation_ledger WHERE scan_id = '{scan_id}' ORDER BY certified_at DESC LIMIT 1"
    res2 = await run_query_tool.run_async(args={"query": read_back_query}, tool_context=None)
    
    if not res2:
        raise RuntimeError("Ledger insertion failed: no row returned on read-back.")
        
    row = {}
    try:
        if isinstance(res2, dict):
            if res2.get("isError"):
                raise RuntimeError(f"Ledger read-back error: {res2}")
            text = res2.get("structuredContent", {}).get("result", "")
            if not text and res2.get("content"):
                text = res2["content"][0].get("text", "")
            
            parsed = json.loads(text)
            if "rows" in parsed and "columns" in parsed:
                if len(parsed["rows"]) > 0:
                    row = dict(zip(parsed["columns"], parsed["rows"][0]))
                else:
                    raise RuntimeError("Ledger insertion failed: no row returned on read-back.")
            elif isinstance(parsed, list) and len(parsed) > 0:
                row = parsed[0]
            else:
                row = parsed
    except Exception as e:
        if not isinstance(e, RuntimeError):
            raise RuntimeError(f"Ledger insertion failed: {e}") from e
        raise

    if abs(float(row.get('measured', 0)) - measured_value) > 0.01 or row.get('frame_start') != frame_start:
        raise RuntimeError(f"Ledger mismatch: inserted {measured_value}, read back {row.get('measured')}")
    
    cert = {
        "scan_id": scan_id,
        "certified_at": row.get('certified_at'),
        "passed": bool(row.get('passed', passed)),
        "frame_start": row.get('frame_start', frame_start),
        "frame_end": row.get('frame_end', frame_end),
        "measured_value": row.get('measured', measured_value),
        "threshold_value": row.get('threshold', 3.0),
        "gemini_estimated_rate": row.get('gemini_estimated_rate', gemini_estimated_rate),
        "cause": row.get('cause', cause),
        "remediation": row.get('remediation', remediation),
        "read_back_query": read_back_query
    }
    
    with open("certificate.json", "w") as f:
        json.dump(cert, f, indent=2)
        
    return cert
