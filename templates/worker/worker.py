"""
Long-poll worker. Pulls jobs from VPS API, runs pipeline, uploads results back.
"""
import os, sys, json, time, requests, traceback
from pathlib import Path
import config
from pipeline import process_job


def auth_headers():
    return {"X-Worker-Token": config.WORKER_TOKEN}


def report_status(job_id: str, status: str, message: str = ""):
    try:
        requests.post(
            f"{config.VPS_API_BASE}/worker/status",
            json={"job_id": job_id, "status": status, "message": message},
            headers=auth_headers(), timeout=10,
        )
    except Exception as e:
        print(f"[status] failed to report: {e}")


def upload_clips(job_id: str, output_dir: str, clip_meta: list):
    """Upload each finished clip to the VPS API."""
    for clip in clip_meta:
        path = clip["file"]
        try:
            with open(path, "rb") as f:
                r = requests.post(
                    f"{config.VPS_API_BASE}/worker/upload/{job_id}",
                    files={"clip": (Path(path).name, f, "video/mp4")},
                    data={
                        "title":    clip.get("title", ""),
                        "caption":  clip.get("caption", ""),
                        "score":    str(clip.get("score", 0)),
                        "reason":   clip.get("reason", ""),
                        "duration": str(clip.get("duration", 0)),
                    },
                    headers=auth_headers(), timeout=300,
                )
                r.raise_for_status()
                print(f"[upload] {Path(path).name} -> {r.status_code}")
        except Exception as e:
            print(f"[upload] failed {path}: {e}")


def pull_one_job():
    """Long-poll for next job."""
    try:
        r = requests.get(
            f"{config.VPS_API_BASE}/worker/pull",
            headers=auth_headers(), timeout=60,
        )
        if r.status_code == 204:
            return None  # no jobs
        r.raise_for_status()
        return r.json()
    except requests.exceptions.Timeout:
        return None
    except Exception as e:
        print(f"[pull] error: {e}")
        return None


def run_loop():
    print(f"[worker] starting, VPS={config.VPS_API_BASE}, poll={config.POLL_INTERVAL_SEC}s")
    while True:
        job = pull_one_job()
        if not job:
            time.sleep(config.POLL_INTERVAL_SEC)
            continue

        job_id = job["id"]
        print(f"\n{'='*60}\n[worker] picked up job {job_id}\n  url: {job['youtube_url']}\n{'='*60}")

        def on_progress(stage, msg):
            report_status(job_id, f"running:{stage}", msg)

        try:
            result = process_job(job, on_progress=on_progress)
            if result["status"] == "done":
                report_status(job_id, "uploading", f"{len(result['clips'])} clips")
                upload_clips(job_id, result["output_dir"], result["clips"])
                report_status(job_id, "done", f"{len(result['clips'])} clips ready")
            else:
                report_status(job_id, "failed", result.get("error", "unknown"))
        except Exception as e:
            tb = traceback.format_exc()
            print(f"[worker] job {job_id} crashed:\n{tb}")
            report_status(job_id, "failed", str(e)[:500])


if __name__ == "__main__":
    if "--once" in sys.argv:
        # Test mode: process one local job
        job = {"id": f"test_{int(time.time())}", "youtube_url": sys.argv[-1]}
        result = process_job(job)
        print(json.dumps({k: v for k, v in result.items() if k != "clips"}, indent=2))
        if result.get("clips"):
            print(f"\nClips ({len(result['clips'])}):")
            for c in result["clips"]:
                print(f"  {Path(c['file']).name} | score={c['score']} | {c['title']}")
    else:
        run_loop()
