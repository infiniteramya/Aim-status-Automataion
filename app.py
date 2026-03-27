import csv
import io
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from queue import Queue, Empty

import pandas as pd
from fastapi import FastAPI, UploadFile, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from automation.engine import AutomationEngine, ProgressEvent

# --- Paths ---
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
RESULTS_DIR = DATA_DIR / "results"
DB_PATH = DATA_DIR / "jobs.db"

for d in [UPLOAD_DIR, RESULTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# --- Database ---

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                total_schools INTEGER DEFAULT 0,
                processed INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                error TEXT
            )
        """)

@contextmanager
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

# --- Job state ---

class JobManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.running_job_id: str | None = None
        self.cancel_flags: dict[str, threading.Event] = {}
        self.subscribers: dict[str, list[Queue]] = {}

    def is_busy(self) -> bool:
        with self.lock:
            return self.running_job_id is not None

    def start_job(self, job_id: str) -> bool:
        with self.lock:
            if self.running_job_id is not None:
                return False
            self.running_job_id = job_id
            self.cancel_flags[job_id] = threading.Event()
            return True

    def finish_job(self, job_id: str):
        with self.lock:
            if self.running_job_id == job_id:
                self.running_job_id = None
            self.cancel_flags.pop(job_id, None)

    def cancel_job(self, job_id: str):
        flag = self.cancel_flags.get(job_id)
        if flag:
            flag.set()

    def is_cancelled(self, job_id: str) -> bool:
        flag = self.cancel_flags.get(job_id)
        return flag.is_set() if flag else False

    def subscribe(self, job_id: str) -> Queue:
        q: Queue = Queue()
        with self.lock:
            self.subscribers.setdefault(job_id, []).append(q)
        return q

    def unsubscribe(self, job_id: str, q: Queue):
        with self.lock:
            subs = self.subscribers.get(job_id, [])
            if q in subs:
                subs.remove(q)

    def publish(self, job_id: str, event: dict):
        with self.lock:
            for q in self.subscribers.get(job_id, []):
                q.put(event)


jobs = JobManager()

# --- FastAPI app ---

app = FastAPI(title="AIM Automation")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/upload")
async def upload_csv(file: UploadFile):
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(400, "Please upload a CSV file")

    content = await file.read()

    # Validate CSV columns
    try:
        text = content.decode("utf-8")
        reader = csv.DictReader(io.StringIO(text))
        cols = [c.strip().lower() for c in (reader.fieldnames or [])]
    except Exception:
        raise HTTPException(400, "Could not parse CSV file")

    col_map = {
        "atl code": "atl",
        "email id": "email",
        "email": "email",
        "atl": "atl",
    }
    normalized = {col_map.get(c, c) for c in cols}
    missing = {"atl", "email"} - normalized
    if missing:
        raise HTTPException(400, f"CSV missing required columns: {', '.join(missing)}")

    # Count rows
    rows = list(csv.DictReader(io.StringIO(text)))
    total = len(rows)
    if total == 0:
        raise HTTPException(400, "CSV has no data rows")

    # Save file and create job
    job_id = uuid.uuid4().hex[:12]
    upload_path = UPLOAD_DIR / f"{job_id}.csv"
    upload_path.write_bytes(content)

    with get_db() as conn:
        conn.execute(
            "INSERT INTO jobs (id, filename, status, total_schools, created_at) VALUES (?, ?, ?, ?, ?)",
            (job_id, file.filename, "pending", total, datetime.utcnow().isoformat()),
        )

    # Preview data
    df = pd.read_csv(io.StringIO(text))
    preview = df.head(10).fillna("").to_dict(orient="records")

    return {
        "job_id": job_id,
        "filename": file.filename,
        "total_schools": total,
        "preview": preview,
        "columns": list(df.columns),
    }


@app.get("/api/jobs")
async def list_jobs():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/jobs/{job_id}/start")
async def start_job(job_id: str):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Job not found")
    if row["status"] == "running":
        raise HTTPException(409, "Job is already running")
    if jobs.is_busy():
        raise HTTPException(409, "Another job is already running. Please wait.")

    if not jobs.start_job(job_id):
        raise HTTPException(409, "Another job is already running")

    with get_db() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'running', started_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), job_id),
        )

    thread = threading.Thread(target=run_automation, args=(job_id,), daemon=True)
    thread.start()

    return {"status": "started", "job_id": job_id}


def run_automation(job_id: str):
    input_path = str(UPLOAD_DIR / f"{job_id}.csv")
    output_path = str(RESULTS_DIR / f"{job_id}_results.csv")

    def on_progress(event: ProgressEvent):
        data = {
            "event": event.event,
            "school_index": event.school_index,
            "total_schools": event.total_schools,
            "school_name": event.school_name,
            "message": event.message,
            "status": event.status,
        }
        jobs.publish(job_id, data)

        # Update processed count in DB
        if event.event == "school_done":
            try:
                with get_db() as conn:
                    conn.execute(
                        "UPDATE jobs SET processed = ? WHERE id = ?",
                        (event.school_index, job_id),
                    )
            except Exception:
                pass

    def cancel_check() -> bool:
        return jobs.is_cancelled(job_id)

    try:
        engine = AutomationEngine(input_path, output_path, on_progress, cancel_check)
        engine.run()

        final_status = "cancelled" if cancel_check() else "completed"
        with get_db() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, finished_at = ? WHERE id = ?",
                (final_status, datetime.utcnow().isoformat(), job_id),
            )
        jobs.publish(job_id, {"event": "done", "message": f"Job {final_status}"})
    except Exception as e:
        with get_db() as conn:
            conn.execute(
                "UPDATE jobs SET status = 'failed', error = ?, finished_at = ? WHERE id = ?",
                (str(e), datetime.utcnow().isoformat(), job_id),
            )
        jobs.publish(job_id, {"event": "error", "message": str(e)})
    finally:
        jobs.finish_job(job_id)


@app.get("/api/jobs/{job_id}/progress")
async def job_progress(job_id: str):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Job not found")

    q = jobs.subscribe(job_id)

    def event_stream():
        try:
            # Send current state
            with get_db() as conn:
                r = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            yield f"data: {json.dumps({'event': 'state', 'status': r['status'], 'processed': r['processed'], 'total_schools': r['total_schools']})}\n\n"

            while True:
                try:
                    event = q.get(timeout=15)
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("event") in ("done", "error"):
                        break
                except Empty:
                    yield f"data: {json.dumps({'event': 'ping'})}\n\n"
        finally:
            jobs.unsubscribe(job_id, q)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/jobs/{job_id}/results")
async def job_results(job_id: str):
    result_path = RESULTS_DIR / f"{job_id}_results.csv"
    if not result_path.exists():
        raise HTTPException(404, "Results not available yet")

    df = pd.read_csv(str(result_path)).fillna("")
    return {
        "rows": df.to_dict(orient="records"),
        "columns": list(df.columns),
        "summary": {
            "total": len(df),
            "approved": int((df["status"] == "APPROVED").sum()) if "status" in df.columns else 0,
            "pending": int(df["status"].str.contains("PENDING", case=False, na=False).sum()) if "status" in df.columns else 0,
            "failed": int(
                df["status"]
                .isin(["LOGIN FAILED", "NGO FORM FAILED", "NGO ID MISSING", "NOT APPROVED"])
                .sum()
            ) if "status" in df.columns else 0,
        },
    }


@app.get("/api/jobs/{job_id}/download")
async def download_results(job_id: str):
    result_path = RESULTS_DIR / f"{job_id}_results.csv"
    if not result_path.exists():
        raise HTTPException(404, "Results not available yet")

    with get_db() as conn:
        row = conn.execute("SELECT filename FROM jobs WHERE id = ?", (job_id,)).fetchone()
    orig_name = row["filename"] if row else "results.csv"
    download_name = f"Results_{orig_name}"

    return FileResponse(
        str(result_path),
        media_type="text/csv",
        filename=download_name,
    )


@app.post("/api/jobs/{job_id}/stop")
async def stop_job(job_id: str):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Job not found")
    if row["status"] != "running":
        raise HTTPException(400, "Job is not running")

    jobs.cancel_job(job_id)
    return {"status": "cancelling", "job_id": job_id}
