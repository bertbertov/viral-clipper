"""
Core viral clipping pipeline.
Stages: download -> transcribe -> find_moments -> for each: cut + reframe + caption + broll + music
"""
import os, json, subprocess, random, re, math, time
from pathlib import Path
import config


# ─── STAGE 1: DOWNLOAD ───────────────────────────────────────────────────────
def download_youtube(url: str, job_id: str) -> str:
    """Download YouTube video at best quality up to 1080p, return path."""
    out_template = os.path.join(config.WORK_DIR, f"{job_id}_source.%(ext)s")
    cmd = [
        "python", "-m", "yt_dlp",
        "--js-runtimes", "node:node",
        "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", out_template,
        url,
    ]
    if os.path.exists(config.COOKIES_FILE):
        cmd.extend(["--cookies", config.COOKIES_FILE])
    print(f"[download] {url}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {r.stderr[-500:]}")
    # Find the actual output file
    candidates = list(Path(config.WORK_DIR).glob(f"{job_id}_source.*"))
    if not candidates:
        raise RuntimeError("yt-dlp produced no output file")
    src = str(candidates[0])
    print(f"[download] -> {src} ({os.path.getsize(src)//1024//1024} MB)")
    return src


# ─── STAGE 2: TRANSCRIBE ─────────────────────────────────────────────────────
_whisper_model = None
def get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        print(f"[whisper] loading {config.WHISPER_MODEL} on {config.WHISPER_DEVICE}/{config.WHISPER_COMPUTE}")
        _whisper_model = WhisperModel(config.WHISPER_MODEL, device=config.WHISPER_DEVICE, compute_type=config.WHISPER_COMPUTE)
    return _whisper_model


def transcribe(video_path: str) -> dict:
    """Returns {language, segments: [{start,end,text,words:[{start,end,word}]}]}."""
    model = get_whisper()
    print(f"[whisper] transcribing {Path(video_path).name}")
    t0 = time.time()
    segments, info = model.transcribe(
        video_path,
        beam_size=5,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )
    out = {"language": info.language, "duration": info.duration, "segments": []}
    for seg in segments:
        words = []
        if seg.words:
            for w in seg.words:
                words.append({"start": w.start, "end": w.end, "word": w.word})
        out["segments"].append({"start": seg.start, "end": seg.end, "text": seg.text.strip(), "words": words})
    print(f"[whisper] {len(out['segments'])} segments, {info.duration:.0f}s of audio in {time.time()-t0:.0f}s")
    return out


# ─── STAGE 3: FIND VIRAL MOMENTS ─────────────────────────────────────────────
def find_viral_moments(transcript: dict) -> list:
    """Use Gemini to identify the most viral 25-60s moments."""
    from google import genai

    # Build a compact transcript with timestamps
    lines = []
    for seg in transcript["segments"]:
        lines.append(f"[{seg['start']:.1f}-{seg['end']:.1f}] {seg['text']}")
    full_text = "\n".join(lines)

    # Truncate if huge (200k chars max for safety)
    if len(full_text) > 200_000:
        full_text = full_text[:200_000] + "\n[TRUNCATED]"

    prompt = f"""You are a short-form video editor finding the most VIRAL moments in this transcript.

Find {config.MAX_CLIPS_PER_VIDEO} short clips (between {config.TARGET_DURATION[0]} and {config.TARGET_DURATION[1]} seconds each) that would perform best on TikTok / Instagram Reels / YouTube Shorts.

Pick moments that have:
- A strong hook in the first 3 seconds (provocative question, surprising claim, emotional spike)
- Self-contained meaning (someone scrolling past should understand without prior context)
- High emotion (humor, shock, controversy, vulnerability, transformation)
- A clear payoff or punchline
- Quotable lines

AVOID: intros, outros, sponsor reads, throat-clearing, low-energy stretches, technical jargon walls, tangents.

Return STRICT JSON only, no markdown, no commentary:
{{
  "clips": [
    {{
      "start": <float seconds>,
      "end": <float seconds>,
      "hook": "first 3-5 words of the hook",
      "title": "punchy 6-10 word title for the clip",
      "caption_idea": "one-line caption for social post",
      "virality_score": <integer 0-100>,
      "reason": "why this moment will go viral (one sentence)"
    }}
  ]
}}

TRANSCRIPT:
{full_text}
"""
    last_err = None
    for key in config.GEMINI_KEYS:
        try:
            client = genai.Client(api_key=key)
            resp = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=prompt,
                config={"response_mime_type": "application/json", "temperature": 0.4},
            )
            data = json.loads(resp.text)
            clips = data.get("clips", [])
            # Sort by virality_score desc
            clips.sort(key=lambda c: c.get("virality_score", 0), reverse=True)
            print(f"[gemini] found {len(clips)} clips, top score: {clips[0]['virality_score'] if clips else 'N/A'}")
            return clips[:config.MAX_CLIPS_PER_VIDEO]
        except Exception as e:
            last_err = e
            print(f"[gemini] key failed: {str(e)[:100]}, trying next...")
    raise RuntimeError(f"All Gemini keys failed: {last_err}")


# ─── STAGE 4: BUILD ASS KARAOKE SUBTITLES ────────────────────────────────────
def ass_color(hex_color: str) -> str:
    """Pass through ASS color (already in BGR &HAABBGGRR& format)."""
    return hex_color


def words_in_range(transcript: dict, start: float, end: float):
    """Return list of word dicts within [start,end] (relative timestamps)."""
    words = []
    for seg in transcript["segments"]:
        for w in seg.get("words", []):
            if w["end"] >= start and w["start"] <= end:
                words.append({
                    "start": max(0, w["start"] - start),
                    "end":   min(end - start, w["end"] - start),
                    "word":  w["word"].strip(),
                })
    return words


def build_ass_karaoke(words: list, ass_path: str, video_w: int, video_h: int):
    """Karaoke-style ASS subtitles with word-by-word highlight, 3-4 words per line."""
    style = (
        f"Style: Default,{config.CAPTION_FONT},{config.CAPTION_SIZE * 4},"
        f"{config.CAPTION_FILL},&H000000FF,{config.CAPTION_OUTLINE},&H64000000,"
        f"-1,0,0,0,100,100,0,0,1,{config.CAPTION_OUTLINE_W},0,2,40,40,400,1"
    )
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {video_w}\nPlayResY: {video_h}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{style}\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    def fmt_t(t):
        h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    # Group into chunks of 3-4 words
    lines = []
    chunk_size = 3
    for i in range(0, len(words), chunk_size):
        group = words[i:i + chunk_size]
        if not group:
            continue
        gstart = group[0]["start"]
        gend   = group[-1]["end"]
        # Build karaoke text: each word with \k duration in centiseconds
        parts = []
        for w in group:
            dur_cs = max(5, int((w["end"] - w["start"]) * 100))
            txt = w["word"].upper().replace("{", "(").replace("}", ")")
            parts.append(f"{{\\k{dur_cs}}}{txt}")
        text = " ".join(parts)
        # ko karaoke style: highlights filled portion with secondary color
        # Use simple approach: full line with rotating highlight via \kf (fill)
        text_kf = text.replace("\\k", "\\kf")
        lines.append(f"Dialogue: 0,{fmt_t(gstart)},{fmt_t(gend)},Default,,0,0,0,,{text_kf}")

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(lines) + "\n")


# ─── STAGE 5: RENDER A CLIP ──────────────────────────────────────────────────
def render_clip(source_video: str, transcript: dict, clip: dict, out_path: str):
    """Cut + reframe to 9:16 + burn karaoke captions + optional b-roll + optional music."""
    start = clip["start"]
    end   = clip["end"]
    dur   = end - start
    job_id = Path(out_path).stem

    # 1. Build ASS karaoke
    words = words_in_range(transcript, start, end)
    ass_path = os.path.join(config.WORK_DIR, f"{job_id}.ass")
    build_ass_karaoke(words, ass_path, config.OUTPUT_W, config.OUTPUT_H)
    # FFmpeg subtitles filter needs forward slashes + escaped colons on Windows
    ass_for_ff = ass_path.replace("\\", "/").replace(":", "\\:")

    # 2. Build filter chain
    # Crop center to 9:16 — assume 1920x1080 input, want 1080x1920 output
    # Take centered 608-wide vertical strip then scale to 1080 wide
    # crop=ih*9/16:ih, then scale to 1080x1920
    if config.ENABLE_BROLL and any(Path(config.BROLL_DIR).glob("*.mp4")):
        broll_files = list(Path(config.BROLL_DIR).glob("*.mp4"))
        broll_path = str(random.choice(broll_files))
        top_h    = int(config.OUTPUT_H * (1.0 - config.BROLL_BOTTOM_RATIO))
        bottom_h = config.OUTPUT_H - top_h
        # Two inputs: source (cropped+scaled to top) + broll (scaled to bottom)
        filter_complex = (
            f"[0:v]crop='ih*9/16':ih,scale={config.OUTPUT_W}:{top_h}:flags=lanczos,"
            f"setsar=1[top];"
            f"[1:v]scale={config.OUTPUT_W}:{bottom_h}:flags=lanczos,setsar=1[bot];"
            f"[top][bot]vstack=inputs=2[stacked];"
            f"[stacked]subtitles='{ass_for_ff}'[v]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start), "-t", str(dur), "-i", source_video,
            "-stream_loop", "-1", "-i", broll_path,
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "0:a:0",
            "-t", str(dur),
            "-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "23",
            "-c:a", "aac", "-b:a", "192k",
            out_path,
        ]
    else:
        filter_complex = (
            f"[0:v]crop='ih*9/16':ih,scale={config.OUTPUT_W}:{config.OUTPUT_H}:flags=lanczos,"
            f"setsar=1,subtitles='{ass_for_ff}'[v]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start), "-t", str(dur), "-i", source_video,
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "0:a:0",
            "-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "23",
            "-c:a", "aac", "-b:a", "192k",
            out_path,
        ]

    print(f"[render] {Path(out_path).name} ({dur:.0f}s) cam_start={start:.1f}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        # Fallback to libx264 if NVENC fails
        print(f"[render] NVENC failed, trying libx264... ({r.stderr[-200:]})")
        cmd_cpu = [c if c not in ("h264_nvenc",) else "libx264" for c in cmd]
        # Replace NVENC-specific flags
        cmd_cpu = [c for c in cmd_cpu if c not in ("p5", "vbr")]
        # Fix preset/cq -> crf
        for i, c in enumerate(cmd_cpu):
            if c == "-cq": cmd_cpu[i] = "-crf"
            if c == "-rc": cmd_cpu[i] = "-preset"
        r2 = subprocess.run(cmd_cpu, capture_output=True, text=True)
        if r2.returncode != 0:
            raise RuntimeError(f"FFmpeg failed: {r2.stderr[-500:]}")
    return out_path


# ─── ORCHESTRATOR ────────────────────────────────────────────────────────────
def process_job(job: dict, on_progress=None) -> dict:
    """Process one job dict {id, youtube_url}. Returns result dict."""
    job_id = job["id"]
    url    = job["youtube_url"]

    def progress(stage, msg=""):
        line = f"[{job_id}] {stage}: {msg}"
        print(line)
        if on_progress:
            on_progress(stage, msg)

    progress("download", url)
    src = download_youtube(url, job_id)

    progress("transcribe", "running faster-whisper")
    transcript = transcribe(src)
    # Save transcript
    with open(os.path.join(config.WORK_DIR, f"{job_id}_transcript.json"), "w", encoding="utf-8") as f:
        json.dump(transcript, f)

    progress("find_moments", "calling Gemini")
    clips = find_viral_moments(transcript)
    if not clips:
        return {"status": "failed", "error": "No clips identified"}

    out_files = []
    job_out_dir = os.path.join(config.OUTPUT_DIR, job_id)
    os.makedirs(job_out_dir, exist_ok=True)

    for i, clip in enumerate(clips, 1):
        progress("render", f"clip {i}/{len(clips)}: {clip.get('title', '')}")
        out_name = f"clip_{i:02d}_{clip.get('virality_score',0):03d}.mp4"
        out_path = os.path.join(job_out_dir, out_name)
        try:
            render_clip(src, transcript, clip, out_path)
            out_files.append({
                "file":     out_path,
                "title":    clip.get("title"),
                "caption":  clip.get("caption_idea"),
                "score":    clip.get("virality_score"),
                "reason":   clip.get("reason"),
                "duration": clip["end"] - clip["start"],
            })
        except Exception as e:
            print(f"[render] clip {i} failed: {e}")

    # Save metadata next to clips
    meta_path = os.path.join(job_out_dir, "_metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({"job_id": job_id, "url": url, "clips": out_files}, f, indent=2)

    return {"status": "done", "clips": out_files, "output_dir": job_out_dir}


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python pipeline.py <youtube_url>")
        sys.exit(1)
    job = {"id": f"local_{int(time.time())}", "youtube_url": sys.argv[1]}
    result = process_job(job)
    print(json.dumps(result, indent=2, default=str))
