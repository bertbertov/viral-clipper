---
name: viral-clipper
description: Build a fully autonomous YouTube short-form clip factory. Discovers viral source videos in user-defined niches, AI-detects the best 25-60s moments, renders 9:16 with karaoke captions on a local GPU, and auto-posts to multiple YouTube channels via official API. Splits across VPS (always-on queue + posting) and laptop (GPU rendering). Use this skill when the user wants automated viral clip generation, OpusClip alternative, AI YouTube automation, or multi-channel content scheduling.
---

# Viral Clipper Skill

Build an end-to-end autonomous pipeline that:
1. Discovers viral-potential YouTube videos in defined niches (daily, no manual input)
2. Downloads, transcribes via faster-whisper on GPU, finds best moments via Gemini
3. Renders 9:16 vertical clips with karaoke captions using FFmpeg + NVENC
4. Auto-posts to YouTube Shorts via official API, with safe rate-limiting
5. Routes different niches to different YouTube channels

## Architecture (split-brain)

```
LAPTOP (GPU)                    VPS (always-on)              Browser
┌────────────────┐              ┌──────────────────┐         ┌───────────────┐
│ worker.py      │  long-poll   │ clips-api        │  HTTPS  │ /clips page   │
│  yt-dlp        │ ◀─────────── │  FastAPI :5903   │ ◀────── │ submit + view │
│  faster-whisper│              │  SQLite queue    │         │ status table  │
│  Gemini        │              │  approval store  │         └───────────────┘
│  NVENC encode  │ ─────────▶   │                  │
│                │  upload MP4  │ clips-poster     │
│ discovery.py   │              │  YouTube API     │ ──────▶ Channel A
│  yt-dlp search │ ─────────▶   │  niche routing   │ ──────▶ Channel B
│  RSS channels  │  POST job    │  rate throttle   │ ──────▶ Channel N
└────────────────┘              └──────────────────┘
```

**Why split:** YouTube bot-detects datacenter IPs (VPS yt-dlp gets blocked). Laptop residential IP works. But VPS must be always-on for queue + posting (laptop sleeps).

## When to use

Trigger this skill when the user asks for any of:
- "Build me a viral clip pipeline / OpusClip clone"
- "Auto-post YouTube Shorts from long videos"
- "AI clip selection from podcasts / interviews"
- "Multi-channel YouTube automation"
- "Klap.app / vidiq alternative"
- "Auto-generate shorts from YouTube channels I follow"

## When NOT to use

- Single one-off video editing → use `video-edit` skill instead
- TikTok-only / Instagram-only → user needs different posting modules
- No GPU available on user's machine → suggest cloud GPU (RunPod) instead

## Prerequisites checklist (verify before building)

```
1. Laptop with NVIDIA GPU (RTX 30/40/50 series, 8GB+ VRAM)
2. Linux VPS with sudo + nginx (Ubuntu 22+ recommended)
3. User has a domain pointing to VPS (for HTTPS subsite + OAuth callbacks)
4. Free Gemini API key — get at https://aistudio.google.com/app/apikey
5. Per channel: Google Cloud project + YouTube Data API v3 enabled + OAuth Desktop client
6. Python 3.11+ on both laptop and VPS
7. FFmpeg with NVENC support on laptop
8. Node.js 18+ on laptop (for yt-dlp js-runtime)
```

## Critical gotchas (DON'T MAKE US REDISCOVER THESE)

These bit me hard during development. Encode them into your build:

### 1. yt-dlp on VPS gets bot-flagged
YouTube returns "Sign in to confirm you're not a bot" for any datacenter IP. Solutions in order of preference:
- **Run yt-dlp on the laptop, not VPS** (this skill's chosen approach)
- Cookies file from a real browser session (`--cookies cookies.txt`)
- Residential proxy ($$$)

### 2. Windows scheduled tasks `/SC ONLOGON` requires admin
For autostart without admin, use the **Startup folder** instead:
- `C:\Users\<USER>\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\`
- Drop a `.vbs` that launches Python silently (window mode 0)

### 3. YouTube OAuth in "Testing" mode = 7-day refresh tokens
Refresh tokens auto-expire weekly. Either:
- Submit app for verification (1-4 weeks, free)
- Re-run OAuth weekly (annoying but free, no review)

### 4. YouTube Data API quota = 6 uploads/day default
Each `videos.insert` = 1,600 quota units, daily limit = 10,000. So **6 uploads max**. Bake throttling in:
- 2h between posts
- max 3 posts/day per channel for new channels (avoid spam flag)
- Request quota bump via Google form to scale

### 5. PowerShell em-dashes break script silently
Em-dashes (`—`), curly quotes (`""`) in PowerShell scripts cause `ParameterBindingArgumentTransformationException`. Use ASCII only.

### 6. Cloudflare caches HTML ~5 min after deploy
Tell user to hard-refresh (Ctrl+Shift+R) or wait 5 min after each `npm run build`.

### 7. yt-dlp needs `--js-runtimes node:node` in 2026
YouTube changed their player to require JS evaluation. Without node, all extractions fail with cryptic errors.

### 8. Pillow 11.2.1 fails on Python 3.14 from-source
Use prebuilt wheels: `pip install --only-binary :all: pillow`

### 9. `ytsearch{N}:` works, `ytsearchdate{N}:` does NOT
The `ytsearchdate` URL scheme isn't recognized — it's just `ytsearch5:query` for top 5 results.

### 10. Channel @handles can collide
`@allin` is the Chamath/Sacks/Friedberg podcast. `@allinpodcast` is a D&D show. Always verify with `yt-dlp --get-title "@HANDLE/videos"` before adding to discovery config.

### 11. SQLite schema migrations need lazy-add
Don't drop tables, ALTER on init:
```python
cols = [r[1] for r in c.execute("PRAGMA table_info(table)").fetchall()]
if "newcol" not in cols:
    c.execute("ALTER TABLE table ADD COLUMN newcol TEXT")
```

### 12. nginx proxy must be added to the HTTPS server block
Look for the existing 443 server block (often in a separate file like `/etc/nginx/sites-available/main`). Adding to the HTTP-only `:80` block will return 404 because traffic comes via HTTPS.

## Build sequence

Always follow this order — earlier steps unblock later ones:

### Phase 1: Verify prerequisites
- `nvidia-smi` to confirm GPU + NVENC
- `ffmpeg -encoders | grep nvenc` to confirm h264_nvenc
- `python -c "from faster_whisper import WhisperModel"` to confirm faster-whisper
- `node --version` to confirm Node 18+
- SSH to VPS works
- Domain DNS resolves to VPS

### Phase 2: VPS API
1. Copy `templates/vps-api/*` to `/opt/clips-api/` on VPS
2. Replace `YOUR-DOMAIN.com` with user's domain
3. `python3 -m venv venv && ./venv/bin/pip install -r requirements.txt`
4. Generate two random tokens:
   ```bash
   WORKER_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
   ADMIN_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
   ```
5. Write `/etc/clips-api.env` with `CLIPS_DATA_DIR`, `CLIPS_WORKER_TOKEN`, `CLIPS_ADMIN_TOKEN`
6. Install + enable `clips-api.service` and `clips-poster.service`
7. Add nginx reverse-proxy block to existing HTTPS server:
   ```
   location /clips-api/ {
       proxy_pass http://127.0.0.1:5903/;
       proxy_set_header Host $host;
       proxy_set_header X-Real-IP $remote_addr;
       proxy_read_timeout 90s;
       client_max_body_size 500M;
   }
   ```
8. Verify: `curl https://USER-DOMAIN/clips-api/health` returns `{"ok":true}`

### Phase 3: Laptop worker
1. Create `C:\Users\<USER>\clips-pipeline\` (Windows) or `~/clips-pipeline/` (Mac/Linux)
2. Copy `templates/worker/*` into a `worker/` subdir
3. Edit `config.py`:
   - Set `VPS_API_BASE` to `https://USER-DOMAIN/clips-api`
   - Set `WORKER_TOKEN` to the value generated above
   - Add Gemini API keys (1+ keys, more = better rate-limit handling)
4. Install Python deps:
   ```bash
   pip install yt-dlp faster-whisper google-genai requests
   ```
5. Test single video:
   ```bash
   python worker.py --once "https://www.youtube.com/watch?v=Ks-_Mh1QhMc"
   ```
   Should produce 6 MP4s in `output/test_TIMESTAMP/`

### Phase 4: Discovery configs
1. Copy `templates/discovery_configs/*.json` to `worker/discovery_configs/`
2. Edit per niche — remove `_comment`, customize:
   - `queries[]` — keyword searches (yt-dlp ytsearch5:query)
   - `channels[]` — `@handles` or `UCxxxx` channel IDs
   - `filters` — duration/age/views/language minimums
   - `limits` — max videos per run, max pending jobs

### Phase 5: YouTube OAuth (per channel)
For EACH channel:
1. Open `https://console.cloud.google.com` signed in as the burner account that owns the channel
2. Create new project named `clips-uploader-NICHE`
3. Enable **YouTube Data API v3**
4. Configure OAuth consent screen (External, scopes: `youtube.upload` + `youtube.readonly`, add the burner email as test user)
5. Create OAuth Client ID, type **Desktop app**, download JSON
6. On laptop, run OAuth helper (browser opens, sign in with burner account):
   ```bash
   python scripts/run_oauth.py
   ```
7. SCP token to VPS: `/etc/clips-tokens/youtube_NICHE.json`
8. `chmod 600 /etc/clips-tokens/youtube_NICHE.json`

### Phase 6: Web UI (optional but recommended)
1. Copy `templates/web/page.tsx` to user's Next.js project at `app/clips/page.tsx`
2. Build + deploy site
3. Sign in at `https://USER-DOMAIN/clips/` with admin token
4. Toggle "Auto-approve" if you want zero-touch posting

### Phase 7: Autostart
1. **Windows worker autostart:** copy `scripts/ClipsWorker.vbs` to `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\`
2. **Discovery daily run:** create scheduled task via `scripts/install_tasks.bat`
3. **VPS services:** already enabled via systemd — `systemctl enable clips-api clips-poster`

### Phase 8: Test E2E
1. Submit one test job via UI (single YouTube URL)
2. Watch worker logs, confirm clips render
3. Manually approve one clip, confirm it posts to YouTube
4. Check the channel's "Your videos" tab — should see the upload

## File reference

| File | Purpose | Where it goes |
|------|---------|---------------|
| `templates/vps-api/app.py` | FastAPI backend | `/opt/clips-api/app.py` on VPS |
| `templates/vps-api/poster.py` | Auto-uploads to YouTube | Same VPS dir |
| `templates/vps-api/clips-api.service` | systemd unit | `/etc/systemd/system/` |
| `templates/vps-api/clips-poster.service` | systemd unit | `/etc/systemd/system/` |
| `templates/worker/pipeline.py` | Core: download→transcribe→Gemini→render | `worker/` on laptop |
| `templates/worker/worker.py` | Long-poll loop | Same laptop dir |
| `templates/worker/discovery.py` | Multi-niche autonomous discovery | Same laptop dir |
| `templates/worker/config.py` | Tokens, paths, model choices | Same laptop dir |
| `templates/discovery_configs/*.json` | Per-niche search topics + channels | `worker/discovery_configs/` |
| `templates/web/page.tsx` | Next.js submit + status UI | User's site at `app/clips/page.tsx` |
| `scripts/ClipsWorker.vbs` | Windows silent autostart | Startup folder |
| `scripts/install_tasks.bat` | Windows scheduled tasks | Run once after setup |
| `scripts/run_oauth.py` | YouTube OAuth flow per channel | Run on laptop with browser |

## Customization knobs

When user asks to tweak behavior, these are the levers:

| What | Where to change |
|------|----------------|
| Title/caption style (clickbait, length, language, tone) | `pipeline.py` → `find_viral_moments()` Gemini prompt |
| Number of clips per source video | `config.py` → `MAX_CLIPS_PER_VIDEO` |
| Clip length range | `config.py` → `TARGET_DURATION = (min_sec, max_sec)` |
| Caption style (font, color, size) | `config.py` → `CAPTION_*` |
| B-roll overlay | `config.py` → `ENABLE_BROLL = True` + drop MP4s in `broll/` |
| Music bed | `config.py` → `ENABLE_MUSIC = True` + drop MP3s in `music/` |
| Whisper accuracy vs speed | `config.py` → `WHISPER_MODEL = "small"` (fast) / `"medium"` (default) / `"large-v3"` (best) |
| Discovery frequency | Windows Task Scheduler trigger; default daily 10am |
| Per-day post limit | VPS `settings` table: `max_posts_per_day` |
| Throttle between posts | VPS `settings` table: `post_throttle_sec` |
| Auto-approve threshold | UI checkbox + min score field |

## Adding a new niche later

1. Drop `worker/discovery_configs/{newniche}.json` (copy an existing one as template)
2. Run OAuth flow signed into the new YouTube account
3. SCP token: `/etc/clips-tokens/youtube_{newniche}.json` on VPS
4. Discovery and posting auto-pick it up — no code changes

## See also

- `docs/SETUP.md` — full step-by-step setup guide
- `docs/TROUBLESHOOTING.md` — common errors and fixes
