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
    res2 = await run_query_tool.run_async(args={"query": f"SELECT * FROM violation_ledger WHERE scan_id = '{scan_id}'"}, tool_context=None)
    
    # We should return a dict based on the read-back values, or just build the cert.
    cert = {
        "scan_id": scan_id,
        "passed": verdict.passed,
        "frame_start": verdict.frame_start,
        "frame_end": verdict.frame_end,
        "measured_value": verdict.measured_value,
        "cause": verdict.cause,
        "remediation": verdict.remediation
    }
    
    with open("certificate.json", "w") as f:
        json.dump(cert, f, indent=2)
        
    return cert
