"""OAuth for the movies/blockbuster YouTube account."""
import json
from google_auth_oauthlib.flow import InstalledAppFlow

CLIENT_SECRET = r"C:\path\to\clips-pipeline\client_secret_NICHE.json"
TOKEN_OUT     = r"C:\path\to\clips-pipeline\youtube_NICHE.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]

flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, scopes=SCOPES)
print("Browser will open — sign in with the MOVIES YouTube account.")
creds = flow.run_local_server(port=8765, prompt="consent", access_type="offline")

with open(TOKEN_OUT, "w") as f:
    json.dump({
        "access_token":  creds.token,
        "refresh_token": creds.refresh_token,
        "client_id":     creds.client_id,
        "client_secret": creds.client_secret,
        "scopes":        creds.scopes,
    }, f, indent=2)
print(f"OK — saved {TOKEN_OUT}")
