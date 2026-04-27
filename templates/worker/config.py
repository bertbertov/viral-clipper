"""Worker config — central settings."""
import os

# VPS API endpoint (clips-api.service)
VPS_API_BASE  = os.getenv("CLIPS_VPS_API",  "https://YOUR-DOMAIN.com/clips-api")
WORKER_TOKEN  = os.getenv("CLIPS_WORKER_TOKEN", "YOUR_WORKER_TOKEN_HERE")

# Local paths
ROOT          = r"C:\Users\A\clips-pipeline"
WORK_DIR      = os.path.join(ROOT, "work")
OUTPUT_DIR    = os.path.join(ROOT, "output")
BROLL_DIR     = os.path.join(ROOT, "broll")
MUSIC_DIR     = os.path.join(ROOT, "music")
COOKIES_FILE  = os.path.join(ROOT, "cookies", "youtube.txt")  # optional

# Gemini (rotating keys from memory project_gemini_keys.md)
GEMINI_KEYS = [
    # Get free keys at https://aistudio.google.com/app/apikey
    "YOUR_GEMINI_API_KEY_1",
    "YOUR_GEMINI_API_KEY_2",  # optional, for rotation
]
GEMINI_MODEL = "gemini-2.5-flash"  # stable, free tier; switch to gemini-3-flash-preview if needed

# Whisper
WHISPER_MODEL = "medium"  # 5x faster, much cooler than large-v3, ~95% as accurate
WHISPER_DEVICE = "cuda"
WHISPER_COMPUTE = "float16"

# Clip generation
TARGET_DURATION  = (25, 60)   # min/max seconds per clip
MAX_CLIPS_PER_VIDEO = 6       # don't make 50 clips from one video
OUTPUT_W, OUTPUT_H = 1080, 1920  # 9:16 vertical

# Caption styling (ASS karaoke)
CAPTION_FONT      = "Arial Black"
CAPTION_SIZE      = 14
CAPTION_FILL      = "&H00FFFFFF"  # white
CAPTION_OUTLINE   = "&H00000000"  # black
CAPTION_HIGHLIGHT = "&H0000FFFF"  # yellow (current word)
CAPTION_OUTLINE_W = 3

# B-roll overlay (None = disabled)
ENABLE_BROLL = True
BROLL_BOTTOM_RATIO = 0.40  # bottom 40% of frame

# Music bed
ENABLE_MUSIC  = False  # off by default — enable when /music/ has files
MUSIC_VOLUME  = 0.10   # 10% of original

# Polling
POLL_INTERVAL_SEC = 30

os.makedirs(WORK_DIR,   exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
