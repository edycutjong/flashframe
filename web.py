import json
import csv
import pandas as pd
import uuid
from contextlib import asynccontextmanager
import asyncio
import os
from fastapi import FastAPI, Request, File, UploadFile, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from mcp.client.stdio import StdioServerParameters
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from dotenv import load_dotenv
import sys
from src.flashframe.cli import run_pipeline

scan_status_dict = {}
clickhouse_client = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global clickhouse_client
    load_dotenv(os.path.expanduser('~/.config/flashframe/clickhouse.env'))
    env = os.environ.copy()
    env.update({
        "CLICKHOUSE_HOST": os.environ.get("CLICKHOUSE_HOST", ""),
        "CLICKHOUSE_USER": os.environ.get("CLICKHOUSE_USER", "default"),
        "CLICKHOUSE_PASSWORD": os.environ.get("CLICKHOUSE_PASSWORD", ""),
        "CLICKHOUSE_DATABASE": os.environ.get("CLICKHOUSE_DATABASE", "flashframe"),
    })
    
    mcp_python = sys.executable
    
    clickhouse = McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=mcp_python,
                args=["-m", "mcp_clickhouse.main"],
                env=env,
            ),
            timeout=60.0
        )
    )
    clickhouse_client = clickhouse
    try:
        tools = await clickhouse.get_tools()
        run_query_tool = next(t for t in tools if t.name == "run_query")
        asyncio.create_task(run_query_tool.run_async(args={"query": "SELECT 1"}, tool_context=None))
    except Exception as e:
        print("Warmup query failed:", e)
        
    yield

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/assets", StaticFiles(directory="assets"), name="assets")
app.mount("/media", StaticFiles(directory="."), name="media")

templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def read_index(request: Request):
    return templates.TemplateResponse(request, "index.html")

async def run_actual_pipeline(scan_id, video_path):
    try:
        await run_pipeline(video_path)
        scan_status_dict[scan_id]["status"] = "complete"
        for s in scan_status_dict[scan_id]["stages"]:
            s["status"] = "done"
    except Exception as e:
        err_str = str(e)
        if "429" in err_str or "quota" in err_str.lower() or "rate limit" in err_str.lower() or "ResourceExhausted" in err_str:
            err_str = "Gemini API rate limit exceeded (free tier quota). A judge may have hit the 5 req/min or 20 req/day limit. Please wait a bit and try again."
        scan_status_dict[scan_id]["status"] = "error"
        scan_status_dict[scan_id]["error_message"] = err_str

@app.post("/upload")
async def upload_file(background_tasks: BackgroundTasks, file: UploadFile = File(None), seed_clip: str = Form(None)):
    scan_id = str(uuid.uuid4())
    scan_status_dict[scan_id] = {
        "status": "running",
        "stages": [
            {"name": "Extract", "status": "pending"},
            {"name": "Ingest", "status": "pending"},
            {"name": "Detect", "status": "pending"},
            {"name": "Adjudicate", "status": "pending"},
            {"name": "Certify", "status": "pending"}
        ]
    }
    video_path = f"assets/{seed_clip}.mp4" if seed_clip else "test_clip.mp4"
    background_tasks.add_task(run_actual_pipeline, scan_id, video_path)
    return RedirectResponse(url=f"/scan/{scan_id}", status_code=303)

@app.get("/scan/{scan_id}", response_class=HTMLResponse)
async def scan_progress(request: Request, scan_id: str):
    return templates.TemplateResponse(request, "progress.html", {"scan_id": scan_id})

@app.get("/api/scan/{scan_id}/status")
async def scan_status(scan_id: str):
    return JSONResponse(scan_status_dict.get(scan_id, {
        "status": "error",
        "error_message": "Scan not found",
        "stages": []
    }))

@app.get("/report/{scan_id}", response_class=HTMLResponse)
async def report(request: Request, scan_id: str):
    with open("certificate.json") as f:
        cert = json.load(f)
        
    global clickhouse_client
    tools = await clickhouse_client.get_tools()
    run_query_tool = next(t for t in tools if t.name == "run_query")
    
    query = f"SELECT frame_idx, yavg FROM frame_metrics WHERE scan_id = '{scan_id}' AND tile = 0 ORDER BY frame_idx"
    try:
        res = await run_query_tool.run_async(args={"query": query}, tool_context=None)
        text = ""
        if hasattr(res, "content") and len(res.content) > 0 and hasattr(res.content[0], "text"):
            text = res.content[0].text
        elif hasattr(res, "text"):
            text = res.text
        elif isinstance(res, list) and len(res) > 0 and hasattr(res[0], "text"):
            text = res[0].text
        elif isinstance(res, dict) and "content" in res:
            text = res["content"][0]["text"]
        else:
            text = str(res)
        data = json.loads(text)
        rows = data.get("rows", data) if isinstance(data, dict) else data
    except Exception as e:
        print("Failed to run query:", e)
        rows = []
        
    df = pd.DataFrame(rows)
    if not df.empty and 'frame_idx' not in df.columns:
        if len(df.columns) >= 2:
            df.columns = ['frame_idx', 'yavg']
    
    width = 1000
    height = 140
    
    if df.empty or 'frame_idx' not in df.columns:
        no_data = True
        polygon_points = ""
        min_frame = 0
        max_frame = 0
        total_frames = 0
    else:
        no_data = False
        max_frame = df['frame_idx'].max()
        min_frame = df['frame_idx'].min()
        total_frames = len(df)
        
        # Binning: min/max binning per pixel column
        df['bin'] = ((df['frame_idx'] - min_frame) / max((max_frame - min_frame), 1) * (width - 1)).astype(int)
        binned = df.groupby('bin').agg(ymax=('yavg', 'max'), ymin=('yavg', 'min')).reset_index()
        
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
        
    if max_frame > min_frame:
        span_x1 = ((cert['frame_start'] - min_frame) / (max_frame - min_frame) * width)
        span_x2 = ((cert['frame_end'] - min_frame) / (max_frame - min_frame) * width)
    else:
        span_x1 = 0
        span_x2 = 0
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
        "max_frame": max_frame,
        "no_data": no_data
    })
