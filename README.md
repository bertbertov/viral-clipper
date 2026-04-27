# Viral Clipper

Autonomous YouTube short-form clip factory. Drop a YouTube URL, get 6 viral 9:16 clips with karaoke captions auto-posted to your channel(s). Zero clicks per day after setup.

**It's like OpusClip — but free, self-hosted, and supports multiple YouTube channels with different niches.**

## What it does

1. **Discovers** new viral source videos every day from configured topics + channels
2. **Downloads** them (yt-dlp on residential IP — bypasses YouTube bot-flag)
3. **Transcribes** with faster-whisper on GPU (8.5× realtime on RTX 5080)
4. **Finds the most viral 25-60s moments** via Gemini (free tier)
5. **Renders 9:16 vertical clips** with karaoke captions via FFmpeg + NVENC
6. **Auto-posts to YouTube Shorts** via official API, throttled to look organic

## Demo result

Test on a 21-min Amy Cuddy TED talk — pipeline picked these moments:

| Score | Title | Length |
|-------|-------|--------|
| 99 | Don't Just Fake It, *Become* It! | 42s |
| 98 | 2 Minutes to Change Your Brain Chemistry | 34s |
| 97 | My Advisor Said: 'Fake It Till You Become It' | 41s |
| 95 | (etc) | 26s |
| 92 | (etc) | 27s |
| 90 | (etc) | 29s |

Total time from submit to all 6 clips ready: **~10 min**.

## Architecture

```
LAPTOP (GPU)                 VPS                          YouTube channels
┌──────────────┐  HTTPS      ┌──────────────────┐         ┌────────────────┐
│ worker.py    │  long-poll  │ clips-api        │  ──────▶│ Channel A      │
│ discovery.py │ ◀────────── │  FastAPI :5903   │         │ (e.g. AI/biz)  │
└──────────────┘             │  SQLite queue    │         └────────────────┘
                             │ clips-poster     │  ──────▶┌────────────────┐
                             │  YouTube API     │         │ Channel B      │
                             │  niche routing   │         │ (e.g. movies)  │
                             └──────────────────┘         └────────────────┘
                                  ▲
                                  │
                             ┌────┴─────┐
                             │ Web UI   │ submit URLs, view status,
                             │ /clips   │ approve clips manually
                             └──────────┘
```

**Why split:** YouTube blocks datacenter IPs. Run yt-dlp on laptop. But VPS must be always-on for queue + scheduled posting.

## Quick install (as a Claude Code skill)

```bash
git clone https://github.com/bertbertov/viral-clipper.git
cp -r viral-clipper/SKILL.md viral-clipper/templates viral-clipper/scripts viral-clipper/docs ~/.claude/skills/viral-clipper/
```

Then in Claude Code:

> "Build me a viral clipper pipeline using the viral-clipper skill — VPS at example.com, niches: business + cooking"

Claude will walk you through the prerequisites, set up VPS services, deploy laptop worker, configure OAuth per channel, and test E2E.

## Manual install

See [docs/SETUP.md](docs/SETUP.md).

## Tech stack

| Layer | Tech |
|-------|------|
| Discovery + worker | Python 3.11+, yt-dlp, faster-whisper (CUDA), google-genai, requests |
| Video pipeline | FFmpeg + NVENC, ASS karaoke subtitles |
| VPS API | FastAPI, SQLite, uvicorn, systemd, nginx reverse proxy |
| Posting | google-api-python-client, official YouTube Data API v3 |
| UI | Next.js 15 static export (any framework works) |

## Costs

- **Gemini**: free tier (250 requests/day per key — pipeline uses ~2/day)
- **YouTube API**: free (6 uploads/day default, request bump for more)
- **VPS**: any Linux box ($5-10/mo)
- **GPU**: yours
- **Total ongoing**: ~$5/mo (just VPS)

vs. OpusClip Pro at $29/mo and Klap at $23/mo, this pays for itself in week 1.

## Limitations / things to know

- Refresh tokens in OAuth Testing mode expire weekly. Either run OAuth weekly, or submit app for verification (free, 1-4 weeks)
- Worker only processes when laptop is on. Discovery and posting run on VPS regardless.
- One worker = one job at a time. Two niches share one queue.
- YouTube quota = 6 uploads/day per channel. Posting is throttled to 3/day for new channels (avoid spam flag).

## Skill metadata

```yaml
name: viral-clipper
description: Build a fully autonomous YouTube short-form clip factory.
```

## License

MIT — fork it, ship it, claim it as your own.

## Credits

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — YouTube extraction
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — CUDA-accelerated Whisper
- [Google Gemini](https://aistudio.google.com/) — viral moment detection
- [FFmpeg](https://ffmpeg.org/) — encoding
