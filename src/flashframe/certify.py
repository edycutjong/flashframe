import json

async def write_certificate(run_query_tool, scan_id, verdict, tile=0):
    write_ledger_sql = f"""
INSERT INTO violation_ledger
SELECT
    '{scan_id}' AS scan_id,
    now() AS certified_at,
    'UK-Ofcom' AS territory,
    {1 if verdict.passed else 0} AS passed,
    {verdict.frame_start},
    {verdict.frame_end},
    {verdict.measured_value},
    (SELECT max_flashes_sec FROM threshold_reference WHERE territory = 'UK-Ofcom' AND criterion = 'flash_rate' LIMIT 1),
    '{verdict.cause.replace("'", "''")}',
    '{verdict.remediation.replace("'", "''")}'
"""
    res = await run_query_tool.run_async(args={"query": write_ledger_sql}, tool_context=None)
    
    # read it back
    read_back_query = f"SELECT * FROM violation_ledger WHERE scan_id = '{scan_id}' ORDER BY certified_at DESC LIMIT 1"
    res2 = await run_query_tool.run_async(args={"query": read_back_query}, tool_context=None)
    
    if not res2:
        raise RuntimeError("Ledger insertion failed: no row returned on read-back.")
        
    if isinstance(res2, str):
        try:
            row = json.loads(res2)
        except json.JSONDecodeError:
            import ast
            row = ast.literal_eval(res2)
    else:
        row = res2

    if isinstance(row, list) and len(row) > 0:
        row = row[0]
    elif isinstance(row, list) and len(row) == 0:
        raise RuntimeError("Ledger insertion failed: no row returned on read-back.")
        
    if isinstance(row, dict) and "isError" in row and row["isError"]:
        raise RuntimeError(f"Ledger read-back error: {row}")

    if row.get('measured') != verdict.measured_value or row.get('frame_start') != verdict.frame_start:
        raise RuntimeError(f"Ledger mismatch: inserted {verdict.measured_value}, read back {row.get('measured')}")
    
    cert = {
        "scan_id": scan_id,
        "certified_at": row.get('certified_at'),
        "passed": bool(row.get('passed', verdict.passed)),
        "frame_start": row.get('frame_start', verdict.frame_start),
        "frame_end": row.get('frame_end', verdict.frame_end),
        "measured_value": row.get('measured', verdict.measured_value),
        "cause": row.get('cause', verdict.cause),
        "remediation": row.get('remediation', verdict.remediation),
        "read_back_query": read_back_query
    }
    
    with open("certificate.json", "w") as f:
        json.dump(cert, f, indent=2)
        
    return cert
