import subprocess
import os
import uuid
import csv

def run_extraction(video_path, fps_override=None, frame_start=None, frame_end=None, scan_id=None):
    scan_id = scan_id or str(uuid.uuid4())
    
    # Generate filtergraph
    filter_txt = "filter.txt"
    with open(filter_txt, "w") as f:
        fps_filter = f"fps={fps_override}," if fps_override else ""
        f.write(f"{fps_filter}split=10[full][t1][t2][t3][t4][t5][t6][t7][t8][t9];\n")
        f.write("[full]signalstats,metadata=print:file=stats_0.txt[out0];\n")
        f.write("[t1]crop=iw/3:ih/3:0:0,signalstats,metadata=print:file=stats_1.txt[out1];\n")
        f.write("[t2]crop=iw/3:ih/3:iw/3:0,signalstats,metadata=print:file=stats_2.txt[out2];\n")
        f.write("[t3]crop=iw/3:ih/3:2*iw/3:0,signalstats,metadata=print:file=stats_3.txt[out3];\n")
        f.write("[t4]crop=iw/3:ih/3:0:ih/3,signalstats,metadata=print:file=stats_4.txt[out4];\n")
        f.write("[t5]crop=iw/3:ih/3:iw/3:ih/3,signalstats,metadata=print:file=stats_5.txt[out5];\n")
        f.write("[t6]crop=iw/3:ih/3:2*iw/3:ih/3,signalstats,metadata=print:file=stats_6.txt[out6];\n")
        f.write("[t7]crop=iw/3:ih/3:0:2*ih/3,signalstats,metadata=print:file=stats_7.txt[out7];\n")
        f.write("[t8]crop=iw/3:ih/3:iw/3:2*ih/3,signalstats,metadata=print:file=stats_8.txt[out8];\n")
        f.write("[t9]crop=iw/3:ih/3:2*iw/3:2*ih/3,signalstats,metadata=print:file=stats_9.txt[out9]\n")

    for i in range(10):
        if os.path.exists(f"stats_{i}.txt"):
            os.remove(f"stats_{i}.txt")
            
    ffmpeg_cmd = ["ffmpeg"]
    if frame_start is not None and frame_end is not None:
        # 25 fps original assumption
        ss = frame_start / 25.0
        t = (frame_end - frame_start + 1) / 25.0
        ffmpeg_cmd.extend(["-ss", str(ss), "-t", str(t)])
        
    ffmpeg_cmd.extend(["-i", video_path])
    
    ffmpeg_cmd.extend([
        "-filter_complex_script", filter_txt,
        "-map", "[out0]", "-map", "[out1]", "-map", "[out2]", "-map", "[out3]", 
        "-map", "[out4]", "-map", "[out5]", "-map", "[out6]", "-map", "[out7]", 
        "-map", "[out8]", "-map", "[out9]", 
        "-f", "null", "-"
    ])
    
    subprocess.run(ffmpeg_cmd, check=True, stderr=subprocess.DEVNULL)
    
    rows = []
    for tile in range(10):
        filename = f"stats_{tile}.txt"
        if not os.path.exists(filename): continue
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
                    frame_idx = int(parts[0].split(':')[1])
                    pts_time = float(parts[2].split(':')[1])
                    
                    if frame_start is not None:
                        # Translate pts_time back to original video's 25fps frame space
                        original_frame_idx = frame_start + int(pts_time * 25.0)
                        frame_idx = original_frame_idx
                    else:
                        # Full video: translate pts_time to 25fps frame index so windowing is consistent
                        frame_idx = int(pts_time * 25.0)
                    
                    current_frame = {'frame_idx': frame_idx, 'pts_time': pts_time}
                elif '=' in line:
                    key, val = line.split('=')
                    if key == 'lavfi.signalstats.YAVG': current_frame['yavg'] = val
                    elif key == 'lavfi.signalstats.YMAX': current_frame['ymax'] = val
                    elif key == 'lavfi.signalstats.YMIN': current_frame['ymin'] = val
                    elif key == 'lavfi.signalstats.SATAVG': current_frame['satavg'] = val
                    elif key == 'lavfi.signalstats.VAVG': 
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
    
    with open('frame_metrics.csv', 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['scan_id', 'frame_idx', 'pts_time', 'tile', 'yavg', 'ymax', 'ymin', 'satavg', 'red_ratio'])
        writer.writerows(rows)
        
    return scan_id
