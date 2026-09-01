import json
import csv
import pandas as pd
import uuid
import tempfile
import subprocess
import base64
from contextlib import asynccontextmanager
import asyncio
import os
from fastapi import FastAPI, Request, File, UploadFile, Form, BackgroundTasks
from fastapi import HTTPException
from starlette.exceptions import HTTPException as StarletteHTTPException
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
    
    missing = [k for k in ["CLICKHOUSE_HOST", "CLICKHOUSE_USER", "CLICKHOUSE_PASSWORD"] if k not in os.environ]
    if missing:
        raise RuntimeError(f"Missing required ClickHouse credentials in environment: {', '.join(missing)}")
        
    env.update({
        "CLICKHOUSE_HOST": os.environ["CLICKHOUSE_HOST"],
        "CLICKHOUSE_USER": os.environ["CLICKHOUSE_USER"],
        "CLICKHOUSE_PASSWORD": os.environ["CLICKHOUSE_PASSWORD"],
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

@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return templates.TemplateResponse(request, "404.html", {"message": "The requested page or scan results could not be found."}, status_code=404)
    return HTMLResponse(content=str(exc.detail), status_code=exc.status_code)


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
    if not seed_clip and (not file or not file.filename):
        return HTMLResponse(content="No video was provided.", status_code=400)

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
    
    if seed_clip:
        video_path = f"assets/{seed_clip}.mp4"
    else:
        video_path = f"{scan_id}_{file.filename}"
        with open(video_path, "wb") as f_out:
            f_out.write(await file.read())
            
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
    global clickhouse_client
    tools = await clickhouse_client.get_tools()
    run_query_tool = next(t for t in tools if t.name == "run_query")
    
    cert_query = f"SELECT * FROM violation_ledger WHERE scan_id = '{scan_id}' ORDER BY certified_at DESC LIMIT 1"
    try:
        cres = await run_query_tool.run_async(args={"query": cert_query}, tool_context=None)
        ctext = ""
        if hasattr(cres, "content") and len(cres.content) > 0 and hasattr(cres.content[0], "text"):
            ctext = cres.content[0].text
        elif hasattr(cres, "text"):
            ctext = cres.text
        elif isinstance(cres, list) and len(cres) > 0 and hasattr(cres[0], "text"):
            ctext = cres[0].text
        elif isinstance(cres, dict) and "content" in cres:
            ctext = cres["content"][0]["text"]
        else:
            ctext = str(cres)
        cdata = json.loads(ctext)
        if "rows" in cdata and "columns" in cdata and len(cdata["rows"]) > 0:
            cert_row = dict(zip(cdata["columns"], cdata["rows"][0]))
        else:
            crows = cdata.get("rows", cdata) if isinstance(cdata, dict) else cdata
            cert_row = crows[0] if crows else {}
        
        if not cert_row:
            raise HTTPException(status_code=404, detail="Scan not found")
        
        cert = {
            "scan_id": scan_id,
            "certified_at": cert_row.get('certified_at', ""),
            "passed": bool(cert_row.get('passed', True)),
            "frame_start": cert_row.get('frame_start', 0),
            "frame_end": cert_row.get('frame_end', 0),
            "measured_value": cert_row.get('measured', 0.0),
            "threshold_value": cert_row.get('threshold', 3.0),
            "cause": cert_row.get('cause', ""),
            "remediation": cert_row.get('remediation', ""),
            "read_back_query": cert_query
        }
    except HTTPException:
        raise
    except Exception as e:
        print("Failed to run cert query:", e)
        cert = {"passed": True, "measured_value": 0.0, "frame_start": 0, "frame_end": 0, "cause": "", "remediation": ""}
    
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
        

    meta_query = f"SELECT source_file, source_fps, measured_fps FROM scan_metadata WHERE scan_id = '{scan_id}' LIMIT 1"
    try:
        mres = await run_query_tool.run_async(args={"query": meta_query}, tool_context=None)
        mtext = ""
        if hasattr(mres, "content") and len(mres.content) > 0 and hasattr(mres.content[0], "text"):
            mtext = mres.content[0].text
        elif hasattr(mres, "text"):
            mtext = mres.text
        elif isinstance(mres, list) and len(mres) > 0 and hasattr(mres[0], "text"):
            mtext = mres[0].text
        elif isinstance(mres, dict) and "content" in mres:
            mtext = mres["content"][0]["text"]
        else:
            mtext = str(mres)
            
        mdata = json.loads(mtext)
        mrows = mdata.get("rows", mdata) if isinstance(mdata, dict) else mdata
        if "rows" in mdata and "columns" in mdata and len(mdata["rows"]) > 0:
            meta_row = dict(zip(mdata["columns"], mdata["rows"][0]))
        else:
            meta_row = mrows[0] if mrows else {}
    except Exception as e:
        print("Failed to run meta query:", e)
        meta_row = {}
        

    source_file = meta_row.get("source_file", "hard_fail_strobe.mp4")
    if source_file == 'unknown':
        source_file = "hard_fail_strobe.mp4"
        
    actual_source_file = source_file
    if not os.path.exists(actual_source_file) and os.path.exists(f"assets/{source_file}"):
        actual_source_file = f"assets/{source_file}"
    elif not os.path.exists(actual_source_file):
        actual_source_file = "test_clip.mp4"
    filmstrip_data = []
    filmstrip_note = None
    
    source_fps = float(meta_row.get("source_fps", 25.0))
    measured_fps = float(meta_row.get("measured_fps", 25.0))
    
    if not cert['passed'] and cert['frame_end'] > cert['frame_start']:
        n_frames = 9
        clip_path = actual_source_file
        
        if not os.path.exists(clip_path) or clip_path == "test_clip.mp4":
            filmstrip_note = "Source footage unavailable for inline preview."
        else:
            try:
                start_seconds = cert['frame_start'] / source_fps
                end_seconds = cert['frame_end'] / source_fps
                duration = max(0.1, end_seconds - start_seconds)
                fps_val = n_frames / duration
                
                with tempfile.TemporaryDirectory() as tmpdir:
                    cmd = [
                        "ffmpeg", "-y", "-ss", str(start_seconds), "-to", str(end_seconds),
                        "-i", clip_path,
                        "-vf", f"fps={fps_val},scale=-1:120",
                        os.path.join(tmpdir, "frame_%d.jpg")
                    ]
                    subprocess.run(cmd, capture_output=True, text=True)
                    
                    for i in range(1, 20):
                        fpath = os.path.join(tmpdir, f"frame_{i}.jpg")
                        if os.path.exists(fpath):
                            with open(fpath, "rb") as f:
                                b64 = base64.b64encode(f.read()).decode("utf-8")
                                filmstrip_data.append(f"data:image/jpeg;base64,{b64}")
                        else:
                            break
                            
            except Exception as e:
                filmstrip_note = "Failed to extract frames for preview."
                
    df = pd.DataFrame(rows)
    if not df.empty and 'frame_idx' not in df.columns:
        if len(df.columns) >= 2:
            df.columns = ['frame_idx', 'yavg']

    # Infer fps and frame counts for older scans without metadata
    if df.empty or 'frame_idx' not in df.columns:
        inferred_measured_fps = 60.0
    else:
        m_frame = df['frame_idx'].max()
        if m_frame > 0:
            inferred_measured_fps = round(len(df) / (m_frame / source_fps))
        else:
            inferred_measured_fps = 10.0

    if 'measured_fps' in meta_row:
        measured_fps = float(meta_row['measured_fps'])
    else:
        measured_fps = float(inferred_measured_fps)

    
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
        "fps": source_fps,
        "measured_fps": measured_fps,
        "source_file": os.path.basename(source_file),
        "min_frame": min_frame,
        "max_frame": max_frame,
        "no_data": no_data,
        "filmstrip_data": filmstrip_data,
        "filmstrip_note": filmstrip_note
    })
