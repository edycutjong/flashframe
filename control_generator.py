import subprocess
import sys

def generate_control():
    total_frames = 138240
    fps = 25
    width = 1280
    height = 720
    dur = total_frames / fps
    
    expr = "mod(N\,255)"
    
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s=16x16:r={fps}:d={dur}",
        "-vf", f"geq=lum='{expr}':cb=128:cr=128,scale={width}:{height}:flags=neighbor",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "control_feature.mp4"
    ]
    
    print("Running ffmpeg for control...")
    subprocess.run(cmd, check=True)
    
if __name__ == "__main__":
    generate_control()
