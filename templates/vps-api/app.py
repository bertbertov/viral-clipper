"""
clips-api: tiny FastAPI service for the viral clipping pipeline.
- Frontend posts jobs here.
- Laptop worker long-polls for jobs and uploads clips.
- Stores everything in SQLite + filesystem.
- Plain CORS open to YOUR-DOMAIN.com only.

Run:
  uvicorn app:app --host 0.0.0.0 --port 5903

Env:
  CLIPS_DATA_DIR=/var/lib/clips
  CLIPS_WORKER_TOKEN=<shared secret with worker>
  CLIPS_ADMIN_TOKEN=<shared secret with frontend>
"""
import os, sqlite3, json, time, uuid, asyncio
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

DATA_DIR     = Path(os.getenv("CLIPS_DATA_DIR", "/var/lib/clips"))
DB_PATH      = DATA_DIR / "jobs.db"
CLIPS_DIR    = DATA_DIR / "clips"
WORKER_TOKEN = os.getenv("CLIPS_WORKER_TOKEN", "change-me-shared-secret")
ADMIN_TOKEN  = os.getenv("CLIPS_ADMIN_TOKEN",  "change-me-admin")

DATA_DIR.mkdir(parents=True, exist_ok=True)
CLIPS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="clips-api")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://YOUR-DOMAIN.com", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── DB ──────────────────────────────────────────────────────────────────────
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                youtube_url TEXT NOT NULL,
                style_preset TEXT,
                status TEXT NOT NULL DEFAULT 'queued',
                message TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                started_at INTEGER,
                completed_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS clips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                title TEXT,
                caption TEXT,
                score INTEGER,
                reason TEXT,
                duration REAL,
                approved INTEGER DEFAULT 0,
                posted_to TEXT,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_clips_job ON clips(job_id);
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        # Lazy migration: add columns if missing
        cols = [r[1] for r in c.execute("PRAGMA table_info(discovered)").fetchall()]
        if cols and "rejected_reason" not in cols:
            c.execute("ALTER TABLE discovered ADD COLUMN rejected_reason TEXT")
        c.commit()


def get_setting(key: str, default: str = "") -> str:
    with db() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default


def set_setting(key: str, value: str):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?,?)", (key, value))
        c.commit()


init_db()


# ─── AUTH ────────────────────────────────────────────────────────────────────
def require_admin(authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None)):
    token = x_admin_token or (authorization.split(" ", 1)[-1] if authorization else None)
    if token != ADMIN_TOKEN:
        raise HTTPException(401, "invalid admin token")


def require_worker(x_worker_token: Optional[str] = Header(None)):
    if x_worker_token != WORKER_TOKEN:
        raise HTTPException(401, "invalid worker token")


# ─── MODELS ──────────────────────────────────────────────────────────────────
class JobIn(BaseModel):
    youtube_url: str
    style_preset: Optional[str] = "default"  # niche routing tag
    max_clips: Optional[int] = None          # override default 6
    extra_hashtags: Optional[str] = None     # appended to every clip's caption
    skip_posting: Optional[bool] = False     # render only, never post
    force_manual_approve: Optional[bool] = False  # bypass auto-approve for this job


class StatusIn(BaseModel):
    job_id: str
    status: str
    message: Optional[str] = ""


# ─── PUBLIC ENDPOINTS (frontend) ─────────────────────────────────────────────
@app.get("/health")
def health():
    return {"ok": True, "ts": int(time.time())}


@app.post("/jobs")
def create_job(body: JobIn, x_admin_token: Optional[str] = Header(None)):
    require_admin(x_admin_token=x_admin_token)
    job_id = uuid.uuid4().hex[:12]
    now = int(time.time())
    # Pack per-job overrides into the message field as JSON
    overrides = {}
    if body.max_clips:            overrides["max_clips"] = body.max_clips
    if body.extra_hashtags:       overrides["extra_hashtags"] = body.extra_hashtags
    if body.skip_posting:         overrides["skip_posting"] = True
    if body.force_manual_approve: overrides["force_manual_approve"] = True
    overrides_json = __import__("json").dumps(overrides) if overrides else None
    with db() as c:
        # Lazy-add overrides column
        cols = [r[1] for r in c.execute("PRAGMA table_info(jobs)").fetchall()]
        if "overrides" not in cols:
            c.execute("ALTER TABLE jobs ADD COLUMN overrides TEXT")
        c.execute(
            "INSERT INTO jobs(id, youtube_url, style_preset, status, created_at, updated_at, overrides) "
            "VALUES (?,?,?,?,?,?,?)",
            (job_id, body.youtube_url, body.style_preset, "queued", now, now, overrides_json),
        )
        c.commit()
    return {"id": job_id, "status": "queued", "overrides": overrides}


@app.get("/jobs")
def list_jobs(x_admin_token: Optional[str] = Header(None), limit: int = 50):
    require_admin(x_admin_token=x_admin_token)
    with db() as c:
        rows = c.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["clip_count"] = c.execute("SELECT COUNT(*) FROM clips WHERE job_id=?", (r["id"],)).fetchone()[0]
            out.append(d)
        return {"jobs": out}


@app.get("/jobs/{job_id}")
def get_job(job_id: str, x_admin_token: Optional[str] = Header(None)):
    require_admin(x_admin_token=x_admin_token)
    with db() as c:
        job = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise HTTPException(404, "not found")
        clips = [dict(r) for r in c.execute("SELECT * FROM clips WHERE job_id=? ORDER BY score DESC", (job_id,)).fetchall()]
        return {"job": dict(job), "clips": clips}


@app.get("/jobs/{job_id}/clips/{clip_id}/file")
def serve_clip(job_id: str, clip_id: int, token: str = ""):
    # Token-in-query for video tag playback
    if token != ADMIN_TOKEN:
        raise HTTPException(401, "invalid token")
    with db() as c:
        row = c.execute("SELECT filename FROM clips WHERE id=? AND job_id=?", (clip_id, job_id)).fetchone()
    if not row:
        raise HTTPException(404, "clip not found")
    path = CLIPS_DIR / job_id / row["filename"]
    if not path.exists():
        raise HTTPException(404, "file missing")
    return FileResponse(path, media_type="video/mp4")


@app.post("/clips/{clip_id}/approve")
def approve_clip(clip_id: int, x_admin_token: Optional[str] = Header(None)):
    require_admin(x_admin_token=x_admin_token)
    with db() as c:
        c.execute("UPDATE clips SET approved=1 WHERE id=?", (clip_id,))
        c.commit()
    return {"ok": True}


@app.delete("/jobs/{job_id}")
def delete_job(job_id: str, x_admin_token: Optional[str] = Header(None)):
    require_admin(x_admin_token=x_admin_token)
    with db() as c:
        c.execute("DELETE FROM clips WHERE job_id=?", (job_id,))
        c.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        c.commit()
    # Cleanup files
    job_dir = CLIPS_DIR / job_id
    if job_dir.exists():
        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)
    return {"ok": True}


# ─── WORKER ENDPOINTS ────────────────────────────────────────────────────────
@app.get("/worker/pull")
async def worker_pull(x_worker_token: Optional[str] = Header(None)):
    require_worker(x_worker_token)
    # Long-poll up to 50s
    for _ in range(25):
        with db() as c:
            row = c.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if row:
                now = int(time.time())
                c.execute(
                    "UPDATE jobs SET status='running', started_at=?, updated_at=? WHERE id=?",
                    (now, now, row["id"]),
                )
                c.commit()
                return dict(row)
        await asyncio.sleep(2)
    return JSONResponse(content=None, status_code=204)


@app.post("/worker/status")
def worker_status(body: StatusIn, x_worker_token: Optional[str] = Header(None)):
    require_worker(x_worker_token)
    now = int(time.time())
    completed = now if body.status in ("done", "failed") else None
    with db() as c:
        if completed:
            c.execute(
                "UPDATE jobs SET status=?, message=?, updated_at=?, completed_at=? WHERE id=?",
                (body.status, body.message, now, completed, body.job_id),
            )
        else:
            c.execute(
                "UPDATE jobs SET status=?, message=?, updated_at=? WHERE id=?",
                (body.status, body.message, now, body.job_id),
            )
        c.commit()
    return {"ok": True}


@app.post("/worker/upload/{job_id}")
def worker_upload(
    job_id: str,
    clip: UploadFile = File(...),
    title: str = Form(""),
    caption: str = Form(""),
    score: int = Form(0),
    reason: str = Form(""),
    duration: float = Form(0.0),
    x_worker_token: Optional[str] = Header(None),
):
    require_worker(x_worker_token)
    job_dir = CLIPS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    dest = job_dir / clip.filename
    with open(dest, "wb") as f:
        while True:
            chunk = clip.file.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    # Pull job overrides
    import json as _json
    overrides = {}
    skip_posting = False
    force_manual = False
    extra_tags = ""
    with db() as c:
        r = c.execute("SELECT overrides FROM jobs WHERE id=?", (job_id,)).fetchone()
        if r and r["overrides"]:
            try:
                overrides = _json.loads(r["overrides"])
                skip_posting = bool(overrides.get("skip_posting"))
                force_manual = bool(overrides.get("force_manual_approve"))
                extra_tags = overrides.get("extra_hashtags", "")
            except Exception:
                pass

    if extra_tags and extra_tags not in caption:
        caption = (caption + " " + extra_tags).strip()

    # Auto-approve if enabled (unless overridden by skip_posting / force_manual)
    auto_approve  = get_setting("auto_approve", "false") == "true"
    min_score     = int(get_setting("auto_approve_min_score", "0"))
    pre_approved  = 0
    if not skip_posting and not force_manual and auto_approve and score >= min_score:
        pre_approved = 1
    # skip_posting marks as -1 (never post even if approved)
    if skip_posting:
        pre_approved = -1

    with db() as c:
        c.execute(
            "INSERT INTO clips(job_id, filename, title, caption, score, reason, duration, approved, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, clip.filename, title, caption, score, reason, duration, pre_approved, int(time.time())),
        )
        c.commit()
    return {"ok": True, "saved_to": str(dest), "approved_state": pre_approved}


# ─── SETTINGS ENDPOINTS ──────────────────────────────────────────────────────
@app.get("/settings")
def list_settings(x_admin_token: Optional[str] = Header(None)):
    require_admin(x_admin_token=x_admin_token)
    with db() as c:
        rows = c.execute("SELECT key, value FROM settings").fetchall()
    out = {r["key"]: r["value"] for r in rows}
    out.setdefault("auto_approve", "false")
    out.setdefault("auto_approve_min_score", "0")
    return out


@app.post("/settings/{key}")
def update_setting(key: str, value: str, x_admin_token: Optional[str] = Header(None)):
    require_admin(x_admin_token=x_admin_token)
    set_setting(key, value)
    return {"ok": True, "key": key, "value": value}
