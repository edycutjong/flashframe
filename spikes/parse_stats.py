import uuid
import os
import csv

scan_id = str(uuid.uuid4())

with open('thresholds.csv', 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['territory', 'criterion', 'max_flashes_sec', 'min_luma_delta', 'screen_area_pct', 'citation'])
    writer.writerow(['UK-Ofcom', 'flash_rate', 3.0, 20.0, 25.0, 'Ofcom Rule 2.12'])
    writer.writerow(['ITU-R-BT1702', 'flash_rate', 3.0, 20.0, 25.0, 'ITU-R BT.1702'])
    writer.writerow(['JP-NAB', 'flash_rate', 3.0, 20.0, 25.0, 'NAB Japan Guidelines'])

def parse_file(tile, filename, rows):
    if not os.path.exists(filename):
        return
    with open(filename, 'r') as f:
        current_frame = {}
        for line in f:
            line = line.strip()
            if line.startswith('frame:'):
                if current_frame and 'yavg' in current_frame:
                    rows.append((
                        scan_id,
                        int(current_frame['frame_idx']),
                        float(current_frame['pts_time']),
                        tile,
                        float(current_frame['yavg']),
                        float(current_frame.get('ymax', 0)),
                        float(current_frame.get('ymin', 0)),
                        float(current_frame.get('satavg', 0)),
                        float(current_frame.get('red_ratio', 0))
                    ))
                parts = line.split()
                frame_idx = parts[0].split(':')[1]
                pts_time = parts[2].split(':')[1]
                current_frame = {'frame_idx': frame_idx, 'pts_time': pts_time}
            elif '=' in line:
                key, val = line.split('=')
                if key == 'lavfi.signalstats.YAVG':
                    current_frame['yavg'] = val
                elif key == 'lavfi.signalstats.YMAX':
                    current_frame['ymax'] = val
                elif key == 'lavfi.signalstats.YMIN':
                    current_frame['ymin'] = val
                elif key == 'lavfi.signalstats.SATAVG':
                    current_frame['satavg'] = val
                elif key == 'lavfi.signalstats.VAVG': 
                    # use V (red chroma) as a crude proxy for red ratio
                    current_frame['red_ratio'] = str(float(val) / 255.0)
        
        if current_frame and 'yavg' in current_frame:
            rows.append((
                scan_id,
                int(current_frame['frame_idx']),
                float(current_frame['pts_time']),
                tile,
                float(current_frame['yavg']),
                float(current_frame.get('ymax', 0)),
                float(current_frame.get('ymin', 0)),
                float(current_frame.get('satavg', 0)),
                float(current_frame.get('red_ratio', 0))
            ))

rows = []
for i in range(10):
    parse_file(i, f'stats_{i}.txt', rows)

with open('frame_metrics.csv', 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['scan_id', 'frame_idx', 'pts_time', 'tile', 'yavg', 'ymax', 'ymin', 'satavg', 'red_ratio'])
    writer.writerows(rows)

print(f"Parsed {len(rows)} rows. Scan ID: {scan_id}")
