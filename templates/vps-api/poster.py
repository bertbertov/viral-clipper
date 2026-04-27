"""
Poster service — uploads approved clips to TikTok / YT Shorts / Instagram Reels.

This is a SCAFFOLD. To activate each platform you need:
- YouTube: OAuth client at console.cloud.google.com (YouTube Data API v3 enabled)
  Run `python poster.py oauth-yt` once to authorize. Token stored in /etc/clips-tokens/yt.json
- TikTok: developer app at developers.tiktok.com, audited Content Posting API
  Run `python poster.py oauth-tt` once. Token stored in /etc/clips-tokens/tt.json
- Instagram: Business account + linked Facebook Page + Meta app with instagram_content_publish
  Page access token in /etc/clips-tokens/ig.json (long-lived 60 days, refresh via cron)

Run as systemd service (clips-poster.service):
  Loops: every 60s, check approved-but-not-yet-posted clips, upload to enabled platforms.
"""
import os, json, sqlite3, time, sys
from pathlib import Path

DATA_DIR  = Path(os.getenv("CLIPS_DATA_DIR", "/var/lib/clips"))
DB_PATH   = DATA_DIR / "jobs.db"
CLIPS_DIR = DATA_DIR / "clips"
TOKEN_DIR = Path("/etc/clips-tokens")

PLATFORMS = ["youtube", "tiktok", "instagram"]


def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def load_token(platform: str, niche: str = "") -> dict | None:
    """Per-niche tokens like youtube_movies.json, fall back to youtube.json."""
    candidates = []
    if niche:
        candidates.append(TOKEN_DIR / f"{platform}_{niche}.json")
    candidates.append(TOKEN_DIR / f"{platform}.json")
    for p in candidates:
        if p.exists():
            return json.loads(p.read_text())
    return None


# ─── YOUTUBE ─────────────────────────────────────────────────────────────────
def post_youtube(clip_path: Path, title: str, caption: str, niche: str = "") -> str:
    """Upload to YouTube Shorts. Returns video URL."""
    tok = load_token("youtube", niche=niche)
    if not tok:
        raise RuntimeError(f"No YouTube token for niche='{niche}'. Add /etc/clips-tokens/youtube_{niche}.json")

    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = Credentials(
        token=tok["access_token"],
        refresh_token=tok.get("refresh_token"),
        client_id=tok["client_id"],
        client_secret=tok["client_secret"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    if not creds.valid:
        creds.refresh(Request())
        # Save refreshed token to whichever file was loaded
        tok["access_token"] = creds.token
        token_path = TOKEN_DIR / (f"youtube_{niche}.json" if niche and (TOKEN_DIR / f"youtube_{niche}.json").exists() else "youtube.json")
        token_path.write_text(json.dumps(tok))

    yt = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {
            "title": (title or "Clip")[:95] + " #Shorts",
            "description": (caption or "") + "\n\n#Shorts",
            "categoryId": "22",  # People & Blogs
        },
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(clip_path), mimetype="video/mp4", resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = req.next_chunk()
    return f"https://youtube.com/shorts/{response['id']}"


# ─── TIKTOK ──────────────────────────────────────────────────────────────────
def post_tiktok(clip_path: Path, title: str, caption: str) -> str:
    """Upload to TikTok via Content Posting API (Direct Post)."""
    tok = load_token("tiktok")
    if not tok:
        raise RuntimeError("No TikTok token. Run: python poster.py oauth-tt")

    import requests
    # Step 1: init upload
    size = clip_path.stat().st_size
    init = requests.post(
        "https://open.tiktokapis.com/v2/post/publish/video/init/",
        headers={
            "Authorization": f"Bearer {tok['access_token']}",
            "Content-Type": "application/json",
        },
        json={
            "post_info": {
                "title": (title + "\n" + (caption or ""))[:2200],
                "privacy_level": "PUBLIC_TO_EVERYONE",
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": size,
                "total_chunk_count": 1,
            },
        },
    )
    init.raise_for_status()
    upload_url = init.json()["data"]["upload_url"]
    publish_id = init.json()["data"]["publish_id"]

    # Step 2: PUT video bytes
    with open(clip_path, "rb") as f:
        put = requests.put(
            upload_url,
            headers={
                "Content-Type": "video/mp4",
                "Content-Length": str(size),
                "Content-Range": f"bytes 0-{size-1}/{size}",
            },
            data=f,
        )
        put.raise_for_status()

    return f"tiktok:{publish_id}"


# ─── INSTAGRAM REELS ─────────────────────────────────────────────────────────
def post_instagram(clip_path: Path, title: str, caption: str, public_url: str) -> str:
    """
    IG Graph API needs a publicly-accessible URL of the video file (not file upload).
    `public_url` should be the https://YOUR-DOMAIN.com/clips-files/... URL.
    """
    tok = load_token("instagram")
    if not tok:
        raise RuntimeError("No Instagram token. Add /etc/clips-tokens/instagram.json")
    import requests

    ig_id  = tok["ig_user_id"]
    access = tok["access_token"]

    # Step 1: create container
    container = requests.post(
        f"https://graph.facebook.com/v21.0/{ig_id}/media",
        params={
            "media_type": "REELS",
            "video_url": public_url,
            "caption": (title + "\n\n" + (caption or ""))[:2200],
            "access_token": access,
        },
    )
    container.raise_for_status()
    creation_id = container.json()["id"]

    # Wait for processing (poll)
    for _ in range(60):
        time.sleep(5)
        s = requests.get(
            f"https://graph.facebook.com/v21.0/{creation_id}",
            params={"fields": "status_code", "access_token": access},
        ).json()
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") == "ERROR":
            raise RuntimeError(f"IG processing error: {s}")

    # Step 2: publish
    pub = requests.post(
        f"https://graph.facebook.com/v21.0/{ig_id}/media_publish",
        params={"creation_id": creation_id, "access_token": access},
    )
    pub.raise_for_status()
    return f"instagram:{pub.json()['id']}"


# ─── MAIN LOOP ───────────────────────────────────────────────────────────────
def get_setting(key: str, default: str = "") -> str:
    with db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default


def get_post_throttle_sec() -> int:
    """Min seconds between consecutive posts to look organic."""
    return int(get_setting("post_throttle_sec", "7200"))  # default 2h between posts


def get_max_posts_per_day() -> int:
    return int(get_setting("max_posts_per_day", "3"))


def posts_in_last_24h() -> int:
    cutoff = int(time.time()) - 86400
    with db() as c:
        return c.execute(
            "SELECT COUNT(*) FROM clips WHERE posted_to IS NOT NULL AND posted_to != '' "
            "AND created_at >= ?", (cutoff,)
        ).fetchone()[0]


def last_post_ts() -> int:
    with db() as c:
        r = c.execute(
            "SELECT MAX(created_at) FROM clips WHERE posted_to IS NOT NULL AND posted_to != ''"
        ).fetchone()
        return r[0] or 0


def post_loop(platforms: list[str], poll_sec: int = 60):
    print(f"[poster] platforms={platforms} poll={poll_sec}s")
    while True:
        try:
            # Rate limiting: don't post if we just posted, or if daily quota hit
            since_last = int(time.time()) - last_post_ts()
            throttle = get_post_throttle_sec()
            today_count = posts_in_last_24h()
            max_today = get_max_posts_per_day()

            if since_last < throttle:
                time.sleep(min(60, throttle - since_last))
                continue
            if today_count >= max_today:
                # Sleep an hour, will re-check
                time.sleep(3600)
                continue

            with db() as c:
                rows = c.execute("""
                    SELECT clips.*, jobs.youtube_url, jobs.style_preset AS niche
                    FROM clips JOIN jobs ON clips.job_id = jobs.id
                    WHERE clips.approved = 1 AND (clips.posted_to IS NULL OR clips.posted_to='')
                    ORDER BY clips.score DESC
                    LIMIT 1
                """).fetchall()
                # approved values: 0=pending, 1=approved-for-post, -1=render-only-never-post

            for row in rows:
                clip_path = CLIPS_DIR / row["job_id"] / row["filename"]
                if not clip_path.exists():
                    continue
                niche = row["niche"] or "default"
                results = {}
                for platform in platforms:
                    try:
                        if platform == "youtube":
                            url = post_youtube(clip_path, row["title"], row["caption"], niche=niche)
                        elif platform == "tiktok":
                            url = post_tiktok(clip_path, row["title"], row["caption"])
                        elif platform == "instagram":
                            public = f"https://YOUR-DOMAIN.com/clips-files/jobs/{row['job_id']}/clips/{row['id']}/file"
                            url = post_instagram(clip_path, row["title"], row["caption"], public)
                        else:
                            continue
                        results[platform] = url
                        print(f"[poster] {platform} {row['filename']} -> {url}")
                    except Exception as e:
                        print(f"[poster] {platform} FAILED: {e}")
                        results[platform] = f"ERROR:{e}"

                with db() as c:
                    c.execute(
                        "UPDATE clips SET posted_to=? WHERE id=?",
                        (json.dumps(results), row["id"]),
                    )
                    c.commit()
        except Exception as e:
            print(f"[poster] loop error: {e}")
        time.sleep(poll_sec)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: poster.py [run|oauth-yt|oauth-tt]")
        sys.exit(1)
    if sys.argv[1] == "run":
        platforms = sys.argv[2:] if len(sys.argv) > 2 else ["youtube"]
        post_loop(platforms)
    elif sys.argv[1] == "oauth-yt":
        # Interactive YouTube OAuth setup
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(
            sys.argv[2],  # path to client_secret.json
            scopes=["https://www.googleapis.com/auth/youtube.upload"],
        )
        creds = flow.run_local_server(port=8765)
        TOKEN_DIR.mkdir(parents=True, exist_ok=True)
        (TOKEN_DIR / "youtube.json").write_text(json.dumps({
            "access_token":  creds.token,
            "refresh_token": creds.refresh_token,
            "client_id":     creds.client_id,
            "client_secret": creds.client_secret,
        }))
        print(f"YouTube OAuth saved to {TOKEN_DIR / 'youtube.json'}")
