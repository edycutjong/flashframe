from fastapi import FastAPI, Request, File, UploadFile, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import json
import csv
import pandas as pd
import uuid

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/assets", StaticFiles(directory="assets"), name="assets")
app.mount("/media", StaticFiles(directory="."), name="media")

templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def read_index(request: Request):
    return templates.TemplateResponse(request, "index.html")

@app.post("/upload")
async def upload_file(file: UploadFile = File(None), seed_clip: str = Form(None)):
    scan_id = "85847671-b150-4ff4-9bde-f13995bbe7a3"
    return RedirectResponse(url=f"/scan/{scan_id}", status_code=303)

@app.get("/scan/{scan_id}", response_class=HTMLResponse)
async def scan_progress(request: Request, scan_id: str):
    return templates.TemplateResponse(request, "progress.html", {"scan_id": scan_id})

@app.get("/api/scan/{scan_id}/status")
async def scan_status(scan_id: str):
    return JSONResponse({
        "status": "complete",
        "stages": [
            {"name": "Extract", "status": "done"},
            {"name": "Ingest", "status": "done"},
            {"name": "Detect", "status": "done"},
            {"name": "Adjudicate", "status": "done"},
            {"name": "Certify", "status": "done"}
        ]
    })

@app.get("/report/{scan_id}", response_class=HTMLResponse)
async def report(request: Request, scan_id: str):
    with open("certificate.json") as f:
        cert = json.load(f)
        
    df = pd.read_csv("frame_metrics.csv")
    df_agg = df.groupby('frame_idx').agg({'ymax': 'max', 'ymin': 'min', 'pts_time': 'min'}).reset_index()
    
    width = 1000
    height = 140
    
    max_frame = df_agg['frame_idx'].max()
    min_frame = df_agg['frame_idx'].min()
    total_frames = len(df_agg)
    
    df_agg['bin'] = ((df_agg['frame_idx'] - min_frame) / (max_frame - min_frame) * (width - 1)).astype(int)
    binned = df_agg.groupby('bin').agg({'ymax': 'max', 'ymin': 'min'}).reset_index()
    
    points_up = []
    points_down = []
    
    for _, row in binned.iterrows():
        x = row['bin']
        y_max_norm = height - (row['ymax'] / 255.0 * height)
        y_min_norm = height - (row['ymin'] / 255.0 * height)
        points_up.append(f"{x},{y_max_norm}")
        points_down.insert(0, f"{x},{y_min_norm}")
        
    polygon_points = " ".join(points_up + points_down)
    
    thresholds = []
    with open("thresholds.csv") as f:
        reader = csv.DictReader(f)
        for row in reader:
            thresholds.append(row)
            
    limit = 3.0
    threshold_y = height - (200 / 255.0 * height)
    
    verdict = "PASS" if cert['passed'] else "FAIL"
    glyph = "✓" if cert['passed'] else "✗"
    if not cert['passed'] and cert['measured_value'] <= limit + 0.5:
        verdict = "BORDERLINE"
        glyph = "⚠"
        
    span_x1 = ((cert['frame_start'] - min_frame) / (max_frame - min_frame) * width)
    span_x2 = ((cert['frame_end'] - min_frame) / (max_frame - min_frame) * width)
    span_width = max(3.0, span_x2 - span_x1)
    
    return templates.TemplateResponse(request, "report.html", {
        "scan_id": scan_id, 
        "cert": cert,
        "polygon_points": polygon_points,
        "width": width,
        "height": height,
        "threshold_y": threshold_y,
        "verdict": verdict,
        "glyph": glyph,
        "limit": limit,
        "span_x": span_x1,
        "span_width": span_width,
        "total_frames": total_frames,
        "fps": 30,
        "min_frame": min_frame,
        "max_frame": max_frame
    })
