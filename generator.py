import json
import random
import subprocess
import sys

def generate():
    total_frames = 138240
    fps = 25
    width = 1280
    height = 720
    
    strobe_frames = 22
    strobe_offset = random.randint(0, total_frames - strobe_frames - 1)
    
    with open("manifest.json", "w") as f:
        json.dump({"strobe_offset_frames": strobe_offset, "strobe_length_frames": strobe_frames}, f)
        
    print(f"Generated manifest.json with offset {strobe_offset}")
    
    # ffmpeg expression:
    # between(N, offset, offset+21) -> 1 if in strobe window, 0 otherwise
    # strobe logic: if(eq(mod(N,4),0), 255, 0)
    # benign ramp: mod(N, 255)
    # combined: if(between(N, offset, offset+21), if(eq(mod(N-offset,4),0),255,0), mod(N,255))
    
    dur = total_frames / fps
    expr = f"if(between(N\,{strobe_offset}\,{strobe_offset+strobe_frames-1})\,if(eq(mod(N-{strobe_offset}\,4)\,0)\,255\,0)\,mod(N\,255))"
    
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s=16x16:r={fps}:d={dur}",
        "-vf", f"geq=lum='{expr}':cb=128:cr=128,scale={width}:{height}:flags=neighbor",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "bench_feature.mp4"
    ]
    
    print("Running ffmpeg...")
    subprocess.run(cmd, check=True)
    
    # Verify with ffprobe
    probe_cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-count_packets", "-show_entries", "stream=nb_read_packets",
        "-of", "csv=p=0", "bench_feature.mp4"
    ]
    res = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
    nb_frames = int(res.stdout.strip())
    print(f"Verified frames: {nb_frames}")
    if nb_frames != total_frames:
        print(f"ERROR: Expected {total_frames} frames, got {nb_frames}")
        sys.exit(1)

if __name__ == "__main__":
    generate()
