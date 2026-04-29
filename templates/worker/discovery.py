"""
Multi-niche YouTube discovery — runs on the LAPTOP.
Reads each discovery_configs/{niche}.json, finds new videos, submits to VPS API tagged with niche.
Each niche has its own queries, channels, filters, limits — and gets posted to a different YouTube channel.
"""
import os, json, sqlite3, subprocess, time, sys
from pathlib import Path
from datetime import datetime, timezone
import requests
import config

CONFIG_DIR = Path(__file__).parent / "discovery_configs"
SEEN_DB    = Path(config.ROOT) / "discovered.db"


def init_seen():
    with sqlite3.connect(SEEN_DB) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS seen (
                video_id    TEXT PRIMARY KEY,
                title       TEXT,
                niche       TEXT,
                source      TEXT,
                source_term TEXT,
                duration    INTEGER,
                views       INTEGER,
                channel     TEXT,
                seen_at     INTEGER NOT NULL,
                queued      INTEGER DEFAULT 0,
                rejected    TEXT,
                job_id      TEXT
            )
        """)
        # lazy-add niche column for older DB
        cols = [r[1] for r in c.execute("PRAGMA table_info(seen)").fetchall()]
        if "niche" not in cols:
            c.execute("ALTER TABLE seen ADD COLUMN niche TEXT")


def already_seen(video_id: str) -> bool:
    with sqlite3.connect(SEEN_DB) as c:
        return c.execute("SELECT 1 FROM seen WHERE video_id=?", (video_id,)).fetchone() is not None


def yt_dlp_search(query: str, n: int) -> list[dict]:
    try:
        out = subprocess.run(
            ["python", "-m", "yt_dlp", "--js-runtimes", "node:node",
             "--flat-playlist",
             "--print", "%(id)s|%(title)s|%(channel)s|%(uploader)s",
             f"ytsearch{n}:{query}"],
            capture_output=True, text=True, timeout=120,
        )
        out_list = []
        for line in out.stdout.strip().splitlines():
            parts = line.split("|", 3)
            if len(parts) >= 2 and len(parts[0]) == 11:
                out_list.append({"id": parts[0], "title": parts[1],
                                "channel": parts[2] if len(parts) > 2 else "",
                                "url": f"https://www.youtube.com/watch?v={parts[0]}"})
        return out_list
    except Exception as e:
        print(f"  [search] '{query}' error: {e}")
        return []


def channel_latest(handle_or_id: str, n: int) -> list[dict]:
    """Accepts @handle, channel ID (UC...), or full URL."""
    h = handle_or_id.strip()
    if h.startswith("http"):
        url = h.rstrip("/") + ("/videos" if not h.endswith("/videos") else "")
    elif h.startswith("UC") and len(h) == 24:
        url = f"https://www.youtube.com/channel/{h}/videos"
    else:
        if not h.startswith("@"):
            h = "@" + h
        url = f"https://www.youtube.com/{h}/videos"
    try:
        out = subprocess.run(
            ["python", "-m", "yt_dlp", "--js-runtimes", "node:node",
             "--flat-playlist", "--playlist-items", f"1:{n}",
             "--print", "%(id)s|%(title)s|%(channel)s",
             url],
            capture_output=True, text=True, timeout=90,
        )
        out_list = []
        for line in out.stdout.strip().splitlines():
            parts = line.split("|", 2)
            if len(parts) >= 2 and len(parts[0]) == 11:
                out_list.append({"id": parts[0], "title": parts[1],
                                "channel": parts[2] if len(parts) > 2 else handle,
                                "url": f"https://www.youtube.com/watch?v={parts[0]}"})
        return out_list
    except Exception as e:
        print(f"  [channel] {handle} error: {e}")
        return []


def fetch_video_meta(video_id: str) -> dict | None:
    try:
        out = subprocess.run(
            ["python", "-m", "yt_dlp", "--js-runtimes", "node:node",
             "--dump-single-json", "--no-download",
             f"https://www.youtube.com/watch?v={video_id}"],
            capture_output=True, text=True, timeout=60,
        )
        if out.returncode != 0:
            return None
        return json.loads(out.stdout)
    except Exception:
        return None


def passes_filters(meta: dict, filters: dict) -> tuple[bool, str]:
    dur = meta.get("duration") or 0
    if dur < filters["min_duration_sec"]: return False, f"too short ({dur}s)"
    if dur > filters["max_duration_sec"]: return False, f"too long ({dur}s)"
    upload_date = meta.get("upload_date")
    if upload_date:
        try:
            upload = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - upload).days
            if age > filters["max_age_days"]: return False, f"too old ({age}d)"
        except ValueError: pass
    if (meta.get("view_count") or 0) < filters["min_views"]: return False, f"low views ({meta.get('view_count')})"
    lang = (meta.get("language") or "").split("-")[0].lower()
    if filters.get("languages") and lang and lang not in filters["languages"]:
        return False, f"lang={lang}"
    return True, "ok"


def topic_match_check(title: str, channel: str, description: str, topic_filter: str) -> tuple[bool, str]:
    """Use Gemini to check if a video matches the niche's topic. Cheap pre-filter."""
    if not topic_filter:
        return True, "no_filter"
    try:
        from google import genai
        prompt = f"""You are filtering YouTube videos for a specific niche.

NICHE TOPIC: {topic_filter}

VIDEO TITLE: {title}
CHANNEL: {channel}
DESCRIPTION (first 500 chars): {description[:500]}

Does this video FIT the niche? Reply with JSON only:
{{"fit": true|false, "reason": "one short sentence"}}
"""
        client = genai.Client(api_key=config.GEMINI_KEYS[0])
        resp = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=prompt,
            config={"response_mime_type": "application/json", "temperature": 0.1},
        )
        import json as _json
        data = _json.loads(resp.text)
        return bool(data.get("fit")), data.get("reason", "")
    except Exception as e:
        # If filter fails, default to allowing (don't block the pipeline on Gemini hiccups)
        return True, f"filter_error: {e}"


def vps_pending_jobs_for_niche(niche: str) -> int:
    try:
        r = requests.get(f"{config.VPS_API_BASE}/jobs?limit=20",
                         headers={"X-Admin-Token": os.getenv("CLIPS_ADMIN_TOKEN", "")},
                         timeout=10)
        if r.status_code == 200:
            return sum(1 for j in r.json()["jobs"]
                       if j["status"] in ("queued", "running") and j.get("style_preset") == niche)
    except Exception: pass
    return 0


def submit_job(url: str, niche: str) -> str | None:
    """Submit job tagged with niche. Propagates CLIPS_AUTO_CROSSPOST flag if set."""
    body = {"youtube_url": url, "style_preset": niche}
    # Stash crosspost intent in extra_hashtags using a sentinel marker the
    # backend recognizes (cleanest way to pass per-job flags without API churn)
    if os.getenv("CLIPS_AUTO_CROSSPOST"):
        body["extra_hashtags"] = "__AUTO_CROSSPOST__"
    try:
        r = requests.post(
            f"{config.VPS_API_BASE}/jobs",
            json=body,
            headers={"X-Admin-Token": os.getenv("CLIPS_ADMIN_TOKEN", "")},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()["id"]
    except Exception as e:
        print(f"  submit failed: {e}")
        return None


def record_seen(video_id, title, niche, source, term, meta, queued, job_id, rejected=""):
    with sqlite3.connect(SEEN_DB) as c:
        c.execute(
            "INSERT OR IGNORE INTO seen(video_id, title, niche, source, source_term, "
            "duration, views, channel, seen_at, queued, rejected, job_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (video_id, title, niche, source, term,
             meta.get("duration", 0) if meta else 0,
             meta.get("view_count", 0) if meta else 0,
             (meta.get("channel") or meta.get("uploader") or "") if meta else "",
             int(time.time()), 1 if queued else 0, rejected, job_id),
        )


def run_niche(niche: str, cfg: dict):
    print(f"\n{'='*60}\n[{niche}] starting\n{'='*60}")
    filters = cfg["filters"]; limits = cfg["limits"]

    pending = vps_pending_jobs_for_niche(niche)
    print(f"  pending {niche} jobs on VPS: {pending}")
    if pending >= limits["max_pending_jobs"]:
        print(f"  saturated, skipping")
        return

    candidates = []
    for q in cfg["queries"]:
        hits = yt_dlp_search(q, limits["max_videos_per_query"])
        kept = sum(1 for h in hits if not already_seen(h["id"]))
        print(f"  [search] '{q}' -> {len(hits)} hits, {kept} new")
        for h in hits:
            if not already_seen(h["id"]):
                candidates.append((h["id"], h["url"], h["title"], "search", q))

    for handle in cfg["channels"]:
        hits = channel_latest(handle, limits["max_videos_per_channel"])
        kept = sum(1 for h in hits if not already_seen(h["id"]))
        print(f"  [channel] {handle} -> {len(hits)} hits, {kept} new")
        for h in hits:
            if not already_seen(h["id"]):
                candidates.append((h["id"], h["url"], h["title"], "channel", handle))

    print(f"  {len(candidates)} candidates")
    queued_now = 0
    available = limits["max_pending_jobs"] - pending
    max_new = min(limits["max_new_videos_per_run"], available)

    topic_filter = cfg.get("topic_filter", "")
    for vid, url, title, source, term in candidates:
        if queued_now >= max_new: break
        meta = fetch_video_meta(vid)
        if not meta:
            print(f"    NO_META {vid} ({title[:50]})")
            record_seen(vid, title, niche, source, term, None, False, None, "no_meta")
            time.sleep(2)
            continue
        ok, reason = passes_filters(meta, filters)
        if not ok:
            print(f"    REJECT  {vid} ({title[:50]}): {reason}")
            record_seen(vid, title, niche, source, term, meta, False, None, reason)
            continue
        # Topic match via Gemini (rejects off-niche videos like music biopics in a movie-action niche)
        if topic_filter:
            fits, why = topic_match_check(meta.get("title", title), meta.get("channel", ""), meta.get("description", ""), topic_filter)
            if not fits:
                print(f"    OFFTOPIC {vid} ({title[:50]}): {why}")
                record_seen(vid, title, niche, source, term, meta, False, None, f"offtopic: {why}")
                continue
        job_id = submit_job(url, niche)
        if job_id:
            queued_now += 1
            print(f"    QUEUE   {vid} (dur={meta.get('duration')}s, views={meta.get('view_count')}): {title[:50]}")
            record_seen(vid, title, niche, source, term, meta, True, job_id)
        else:
            record_seen(vid, title, niche, source, term, meta, False, None, "submit_failed")

    print(f"  [{niche}] queued {queued_now} jobs")


def run():
    if not os.getenv("CLIPS_ADMIN_TOKEN"):
        print("ERROR: set CLIPS_ADMIN_TOKEN env var"); sys.exit(1)
    init_seen()

    configs = sorted(CONFIG_DIR.glob("*.json"))
    if not configs:
        print(f"No configs found in {CONFIG_DIR}"); return
    print(f"Niches: {[c.stem for c in configs]}")

    for cfg_path in configs:
        try:
            cfg = json.loads(cfg_path.read_text())
            run_niche(cfg_path.stem, cfg)
        except Exception as e:
            print(f"[{cfg_path.stem}] ERROR: {e}")


if __name__ == "__main__":
    run()
