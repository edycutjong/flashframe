import os
import json
import subprocess
import time
import requests
import statistics

# Clickhouse config
CH_HOST = os.environ.get("CLICKHOUSE_HOST")
CH_PORT = os.environ.get("CLICKHOUSE_PORT", "8443")
CH_USER = os.environ.get("CLICKHOUSE_USER", "default")
CH_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD")
CH_DATABASE = os.environ.get("CLICKHOUSE_DATABASE")

def ch_query(query, data=None):
    url = f"https://{CH_HOST}:{CH_PORT}/?database={CH_DATABASE}"
    auth = (CH_USER, CH_PASSWORD)
    if data is not None:
        res = requests.post(url, auth=auth, params={"query": query}, data=data)
    else:
        res = requests.post(url, auth=auth, data=query)
    if res.status_code != 200:
        raise Exception(f"ClickHouse error: {res.text}")
    return res.text

def extract_metrics():
    if not os.path.exists("metrics_with_id.csv"):
        print("Extracting metrics using ffprobe...")
        cmd = [
            "ffprobe", "-f", "lavfi",
            "-i", "movie=bench_feature.mp4,signalstats",
            "-show_entries", "frame_tags=lavfi.signalstats.YAVG",
            "-of", "csv=p=0"
        ]
        with open("metrics.csv", "w") as f:
            subprocess.run(cmd, stdout=f, check=True)
        print("Adding frame_id...")
        subprocess.run("awk '{print NR-1 \",\" $0}' metrics.csv > metrics_with_id.csv", shell=True, check=True)
    else:
        print("metrics_with_id.csv already exists, skipping extraction.")

def run_benchmarks():
    print("Warming ClickHouse...")
    try:
        ch_query("SELECT 1")
    except Exception as e:
        print(f"Warmup error: {e}")
    
    with open("metrics_with_id.csv", "rb") as f:
        csv_data = f.read()
    
    N = 5
    ingest_times = []
    detect_times = []
    total_times = []
    
    with open("manifest.json") as f:
        manifest = json.load(f)
    expected_offset = manifest["strobe_offset_frames"]
    
    detect_sql = """
    SELECT frame_id
    FROM (
        SELECT
            frame_id,
            groupArray(22)(yavg) OVER (ORDER BY frame_id ROWS BETWEEN CURRENT ROW AND 21 FOLLOWING) AS w
        FROM frames
    )
    WHERE length(w) = 22
      AND w[1] > 200 AND w[2] < 50 AND w[3] < 50 AND w[4] < 50
      AND w[5] > 200 AND w[6] < 50 AND w[7] < 50 AND w[8] < 50
      AND w[9] > 200 AND w[10] < 50 AND w[11] < 50 AND w[12] < 50
      AND w[13] > 200 AND w[14] < 50 AND w[15] < 50 AND w[16] < 50
      AND w[17] > 200 AND w[18] < 50 AND w[19] < 50 AND w[20] < 50
      AND w[21] > 200 AND w[22] < 50
    ORDER BY frame_id
    LIMIT 1
    """
    
    for i in range(N):
        print(f"Iteration {i+1}/{N}...")
        
        # Cleanup
        ch_query("DROP TABLE IF EXISTS frames")
        ch_query("CREATE TABLE frames (frame_id UInt32, yavg Float32) ENGINE = MergeTree() ORDER BY frame_id")
        
        t0 = time.time()
        
        # Ingest
        ch_query("INSERT INTO frames FORMAT CSV", data=csv_data)
        t_ingest = time.time()
        
        # Detect
        res = ch_query(detect_sql)
        t_detect = time.time()
        
        ingest_time = t_ingest - t0
        detect_time = t_detect - t_ingest
        total_time = t_detect - t0
        
        ingest_times.append(ingest_time)
        detect_times.append(detect_time)
        total_times.append(total_time)
        
        detected_offset = int(res.strip()) if res.strip() else -1
        print(f"  Detected: {detected_offset}, Expected: {expected_offset}")
        if detected_offset != expected_offset:
            print(f"  WARNING: Mismatch!")

    def p50(data):
        return statistics.median(data)
    
    def p95(data):
        return statistics.quantiles(data, n=100)[94]
    
    print("\nBENCHMARK RESULTS (seconds):")
    print(f"INGEST: p50 = {p50(ingest_times):.3f} / p95 = {p95(ingest_times):.3f}")
    print(f"DETECT: p50 = {p50(detect_times):.3f} / p95 = {p95(detect_times):.3f}")
    print(f"TOTAL:  p50 = {p50(total_times):.3f} / p95 = {p95(total_times):.3f}")

if __name__ == "__main__":
    extract_metrics()
    run_benchmarks()
