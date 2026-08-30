import subprocess
import requests
import os

CH_HOST = os.environ.get("CLICKHOUSE_HOST")
CH_PORT = os.environ.get("CLICKHOUSE_PORT", "8443")
CH_USER = os.environ.get("CLICKHOUSE_USER", "default")
CH_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD")
CH_DATABASE = os.environ.get("CLICKHOUSE_DATABASE")

def ch_query(query, data=None):
    url = f"https://{CH_HOST}:{CH_PORT}/?database={CH_DATABASE}"
    auth = (CH_USER, CH_PASSWORD)
    if data:
        res = requests.post(url, auth=auth, params={"query": query}, data=data)
    else:
        res = requests.post(url, auth=auth, data=query)
    if res.status_code != 200:
        raise Exception(f"ClickHouse error: {res.text}")
    return res.text

def run_control():
    print("Extracting control metrics...")
    cmd = [
        "ffprobe", "-f", "lavfi",
        "-i", "movie=control_feature.mp4,signalstats",
        "-show_entries", "frame_tags=lavfi.signalstats.YAVG",
        "-of", "csv=p=0"
    ]
    with open("control_metrics.csv", "w") as f:
        subprocess.run(cmd, stdout=f, check=True)
    subprocess.run("awk '{print NR-1 \",\" $0}' control_metrics.csv > control_metrics_with_id.csv", shell=True, check=True)
    
    with open("control_metrics_with_id.csv", "rb") as f:
        csv_data = f.read()
    
    print("Ingesting control metrics...")
    ch_query("DROP TABLE IF EXISTS frames_control")
    ch_query("CREATE TABLE frames_control (frame_id UInt32, yavg Float32) ENGINE = MergeTree() ORDER BY frame_id")
    ch_query("INSERT INTO frames_control FORMAT CSV", data=csv_data)
    
    print("Running detection on control...")
    detect_sql = """
    SELECT count(*)
    FROM (
        SELECT
            frame_id,
            groupArray(22)(yavg) OVER (ORDER BY frame_id ROWS BETWEEN CURRENT ROW AND 21 FOLLOWING) AS w
        FROM frames_control
    )
    WHERE length(w) = 22
      AND w[1] > 200 AND w[2] < 50 AND w[3] < 50 AND w[4] < 50
      AND w[5] > 200 AND w[6] < 50 AND w[7] < 50 AND w[8] < 50
      AND w[9] > 200 AND w[10] < 50 AND w[11] < 50 AND w[12] < 50
      AND w[13] > 200 AND w[14] < 50 AND w[15] < 50 AND w[16] < 50
      AND w[17] > 200 AND w[18] < 50 AND w[19] < 50 AND w[20] < 50
      AND w[21] > 200 AND w[22] < 50
    """
    res = ch_query(detect_sql)
    count = int(res.strip())
    print(f"False positive count: {count}")

if __name__ == "__main__":
    run_control()
