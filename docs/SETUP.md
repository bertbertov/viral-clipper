# Setup Guide

Step-by-step setup. Total time: ~1 hour first time, ~10 min for each additional niche/channel.

## Prerequisites

```
☐ Linux VPS (Ubuntu 22+) with sudo access
☐ Domain pointing to VPS via Cloudflare or A record
☐ nginx already installed and serving HTTPS for your domain
☐ Laptop with NVIDIA GPU (8GB+ VRAM, RTX 30/40/50)
☐ Python 3.11+ on both laptop and VPS
☐ Node.js 18+ on laptop
☐ FFmpeg with NVENC support on laptop (verify: `ffmpeg -encoders | grep nvenc`)
☐ Free Gemini API key from https://aistudio.google.com/app/apikey
☐ Burner Google account per channel you want to auto-post to
```

## Phase 1 — VPS API

### 1.1 Generate shared tokens

On laptop:
```bash
WORKER_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
ADMIN_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
echo "WORKER: $WORKER_TOKEN"
echo "ADMIN:  $ADMIN_TOKEN"
# Save somewhere safe — you'll paste both into the UI later
```

### 1.2 Deploy to VPS

```bash
ssh root@your-vps "mkdir -p /opt/clips-api /var/lib/clips/clips"
scp templates/vps-api/* root@your-vps:/opt/clips-api/
ssh root@your-vps << EOF
cd /opt/clips-api
# Replace YOUR-DOMAIN.com placeholder
sed -i 's|YOUR-DOMAIN.com|yourdomain.com|g' app.py poster.py
python3 -m venv venv
./venv/bin/pip install -r requirements.txt google-auth-oauthlib google-api-python-client
echo 'CLIPS_DATA_DIR=/var/lib/clips' > /etc/clips-api.env
echo "CLIPS_WORKER_TOKEN=$WORKER_TOKEN" >> /etc/clips-api.env
echo "CLIPS_ADMIN_TOKEN=$ADMIN_TOKEN"   >> /etc/clips-api.env
chmod 600 /etc/clips-api.env
cp clips-api.service clips-poster.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now clips-api
EOF
```

### 1.3 Add nginx reverse proxy

Find your existing HTTPS server block. Common locations:
- `/etc/nginx/sites-available/yourdomain.com`
- `/etc/nginx/sites-enabled/default`

Add inside the `server { listen 443 ssl; ... }` block:

```nginx
location /clips-api/ {
    proxy_pass http://127.0.0.1:5903/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-Proto https;
    proxy_read_timeout 90s;
    client_max_body_size 500M;
}
```

Reload nginx:
```bash
ssh root@your-vps "nginx -t && systemctl reload nginx"
```

### 1.4 Verify

```bash
curl https://yourdomain.com/clips-api/health
# Expected: {"ok": true, "ts": ...}
```

## Phase 2 — Laptop worker

### 2.1 Setup directory + Python deps

```bash
mkdir -p ~/clips-pipeline
cp -r templates/worker ~/clips-pipeline/
cp -r templates/discovery_configs ~/clips-pipeline/worker/
mkdir -p ~/clips-pipeline/{work,output,broll,music,cookies}

pip install yt-dlp faster-whisper google-genai requests scipy numpy opencv-python
```

### 2.2 Configure

Edit `~/clips-pipeline/worker/config.py`:

```python
VPS_API_BASE = "https://yourdomain.com/clips-api"
WORKER_TOKEN = "PASTE_WORKER_TOKEN_FROM_PHASE_1"

GEMINI_KEYS = [
    "AIza...your_first_key",
    "AIza...your_second_key",  # optional, for rate-limit rotation
]
```

### 2.3 Edit discovery configs

Each `discovery_configs/{niche}.json` file controls one niche. Either keep the example or edit:
- `queries[]` — keyword search terms
- `channels[]` — `@handle` or `UC...ID` of channels to monitor
- `filters` — duration, age, view count thresholds
- `limits` — max videos per run

### 2.4 Test single video

```bash
cd ~/clips-pipeline/worker
python worker.py --once "https://www.youtube.com/watch?v=Ks-_Mh1QhMc"
```

Should produce 6 MP4 files in `output/test_TIMESTAMP/` within ~10 min. If yes, the GPU pipeline works.

### 2.5 Run the long-poll worker

```bash
python worker.py
# Logs: "[worker] starting, VPS=..., poll=30s"
```

Now any job submitted via the API gets picked up.

## Phase 3 — YouTube OAuth (per channel)

Repeat this for each YouTube channel you want to auto-post to.

### 3.1 Google Cloud Console

1. Open https://console.cloud.google.com signed in as the **burner account that owns the channel**
2. Create new project: `clips-uploader-NICHE`
3. Search bar → **YouTube Data API v3** → Enable
4. Left menu → **APIs & Services** → **OAuth consent screen**
   - User type: External
   - App name: `clips-uploader-NICHE`
   - Developer contact email: yours
   - Scopes: `youtube.upload` and `youtube.readonly`
   - Test users: add the burner Gmail
5. Left menu → **Credentials** → **+ CREATE CREDENTIALS** → **OAuth client ID**
   - Application type: **Desktop app**
   - Name: `clips-cli-NICHE`
6. Download the JSON, save as `client_secret_NICHE.json`

### 3.2 Run OAuth helper

Edit `scripts/run_oauth.py`:
- `CLIENT_SECRET` → path to your downloaded JSON
- `TOKEN_OUT` → e.g. `youtube_NICHE.json`

```bash
python scripts/run_oauth.py
# Browser opens. Sign in with the burner account. Approve the scopes.
# Token saved as youtube_NICHE.json
```

### 3.3 Ship token to VPS

```bash
ssh root@your-vps "mkdir -p /etc/clips-tokens && chmod 700 /etc/clips-tokens"
scp youtube_NICHE.json root@your-vps:/etc/clips-tokens/
ssh root@your-vps "chmod 600 /etc/clips-tokens/youtube_NICHE.json"
```

### 3.4 Enable poster service

```bash
ssh root@your-vps "systemctl enable --now clips-poster && systemctl status clips-poster"
```

## Phase 4 — Web UI (optional but recommended)

If you have a Next.js site already (e.g. yourdomain.com):

```bash
cp templates/web/page.tsx /path/to/your-nextjs/app/clips/page.tsx
cd /path/to/your-nextjs
npm run build
# Deploy via your normal deploy script
```

Open `https://yourdomain.com/clips/` → paste the ADMIN token → sign in.

If you don't have a Next.js site, the API works fine via curl — just no GUI for approving clips.

## Phase 5 — Autostart

### Windows (laptop)

Drop `scripts/ClipsWorker.vbs` into:
```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\
```

Edit the VBS to point to your `worker.py` path. Worker now starts silently every time you log in.

For daily discovery:
```
scripts/install_tasks.bat
```
Creates a Task Scheduler task that runs `discovery.py` daily at 10am.

### Linux/Mac (laptop)

Add to your shell startup or write a systemd user unit:
```bash
nohup python ~/clips-pipeline/worker/worker.py > /tmp/clips.log 2>&1 &
```

For daily discovery:
```bash
crontab -e
# Add:
0 10 * * * cd ~/clips-pipeline/worker && python discovery.py
```

## Verification checklist

```
☐ curl https://yourdomain.com/clips-api/health → {"ok":true}
☐ Worker is running (check process list / "long-poll" log)
☐ /etc/clips-tokens/youtube_NICHE.json exists per channel
☐ Submit one test URL via UI → job appears in list
☐ Job transitions: queued → running:download → running:transcribe → running:render → done
☐ 6 clips appear in UI for that job
☐ Manually approve one clip → check YouTube channel → video uploaded
☐ Poster log shows "[poster] youtube clip_X.mp4 -> https://youtube.com/shorts/..."
```

If all 8 boxes check, the pipeline is fully operational. Add more niches by repeating Phase 3 with a new burner channel.
