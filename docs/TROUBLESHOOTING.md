# Troubleshooting

Common errors and proven fixes.

## "Sign in to confirm you're not a bot" (yt-dlp)

YouTube blocked your IP. If on VPS — move discovery/download to laptop (residential IP). If laptop also blocked, options:
1. Export browser cookies: `yt-dlp --cookies-from-browser chrome`
2. Wait 24h
3. Use a residential proxy

## OAuth: "App has not completed Google verification"

Add your email as a **Test user** in OAuth consent screen settings. Test users can use Testing-mode apps without full verification.

## OAuth refresh token expired (after 7 days)

Apps in Testing mode auto-expire refresh tokens weekly. Either:
- Re-run `python scripts/run_oauth.py`
- Submit app for verification (free, takes 1-4 weeks)

## Worker keeps polling but never picks up jobs

Check:
- `WORKER_TOKEN` in `config.py` matches `CLIPS_WORKER_TOKEN` in `/etc/clips-api.env` on VPS
- VPS clips-api is running: `ssh root@vps "systemctl status clips-api"`
- nginx proxy is configured in HTTPS server block (not just :80)
- Test pull manually: `curl -H "X-Worker-Token: $WORKER_TOKEN" https://yourdomain/clips-api/worker/pull`

## "Access is denied" creating Windows scheduled task

`schtasks /SC ONLOGON` requires admin. Use the **Startup folder** approach instead — drop a `.vbs` in `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\`.

## "ParameterBindingArgumentTransformationException" in PowerShell

Your script has em-dashes (—) or curly quotes (""). Replace with ASCII (-) and (") — even comments will break PowerShell parsing.

## yt-dlp returns "Unsupported url scheme: ytsearchdate"

`ytsearchdate` was removed. Use `ytsearch5:your query` instead.

## Pillow build fails on Python 3.14

Use prebuilt wheel:
```bash
pip install --only-binary :all: pillow
```

## InsightFace pip install fails

Needs C++ build tools. Use MediaPipe FaceLandmarker instead — simpler, comparable quality.

## Cloudflare returns 404 for new /clips/ page

Cloudflare caches HTML for ~5 min after deploy. Either:
- Hard-refresh: Ctrl+Shift+R
- Bust cache via Cloudflare dashboard
- Wait 5 min

## Worker ran out of VRAM

Switch to a smaller Whisper model:
```python
WHISPER_MODEL = "medium"  # was "large-v3", saves ~2GB
# or "small" for ~3GB savings, slightly less accurate
```

## YouTube upload returns 403 "youtubeSignupRequired"

The Google account hasn't created a YouTube channel. Visit youtube.com signed in as that account, click your profile → Create Channel.

## YouTube quota exceeded

Default: 10,000 units/day = ~6 uploads. Either:
1. Wait 24h for reset
2. Submit quota bump request via Google form (free, 1-2 weeks)
3. Use a second project's API key

## "Channel does not have a videos tab" with @handle

Wrong handle. Verify:
```bash
yt-dlp --flat-playlist --playlist-items 1 --get-title "https://www.youtube.com/@HANDLE/videos"
```
If it errors, find the right handle by visiting the channel page in browser → look at URL.

## Clips uploaded but not appearing as Shorts

YouTube classifies as Short if: ≤60s, vertical 9:16, has #Shorts in title or description. Check `poster.py` is appending #Shorts (it does by default).

## Posts look spammy / channel got flagged

Lower `max_posts_per_day` to 1-2 for new accounts:
```sql
ssh root@vps "sqlite3 /var/lib/clips/jobs.db \"UPDATE settings SET value='1' WHERE key='max_posts_per_day';\""
```
And increase throttle:
```sql
UPDATE settings SET value='14400' WHERE key='post_throttle_sec';  -- 4h between
```
