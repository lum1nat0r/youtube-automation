#!/usr/bin/env python3
"""
Lumi's Lane YouTube Uploader — lädt Videos mit Metadaten-Datei hoch.

Nutzung (im Container):
  upload.py --auth                      # einmalige OAuth-Autorisierung
  upload.py video.mp4 metadata.md       # Upload mit Metadaten aus .md
  upload.py video.mp4 metadata.md --thumb thumb.png --privacy unlisted

Metadaten-Datei (unser Lumi's Lane-Format):
  Zeile 1   = Titel
  Zeilen 2+ = Beschreibung (freier Text, Leerzeilen ok)
  letzte Zeile = Hashtags (space-separated, mit #)
"""
import argparse
import os
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
CLIENT_SECRET = "/config/client_secret.json"
TOKEN = "/config/token.json"
CATEGORY_MOTORCYCLE = "2"  # Autos & Fahrzeuge


def auth_manual(flow):
    """Code-basierte Autorisierung (kein localhost-Server — läuft remote)."""
    flow.redirect_uri = "http://localhost"  # muss explizit gesetzt sein (sonst 400: missing redirect_uri)
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    print("\nÖffne diese URL im Browser (eingeloggt mit dem Lumi's Lane-Konto):")
    print(auth_url)
    print("\nNach der Freigabe zeigt der Browser eine 'Seite nicht erreichbar'-Adresse")
    print("mit http://localhost/?code=... — kopiere den CODE (alles nach 'code=')")
    code = input("Code: ").strip()
    flow.fetch_token(code=code)
    return flow.credentials


def get_credentials():
    creds = None
    if os.path.exists(TOKEN):
        creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRET):
                sys.exit(f"FEHLER: {CLIENT_SECRET} fehlt — client_secret.json in /config legen")
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
            creds = auth_manual(flow)
        os.makedirs(os.path.dirname(TOKEN), exist_ok=True)
        with open(TOKEN, "w") as f:
            f.write(creds.to_json())
        print("Token gespeichert:", TOKEN)
    return creds


def parse_metadata(md_path):
    """Parst unsere Lumi's Lane-Metadaten-Datei."""
    with open(md_path, encoding="utf-8") as f:
        lines = [l.rstrip() for l in f if l.strip()]
    if not lines:
        sys.exit("FEHLER: Metadaten-Datei ist leer")
    title = lines[0].lstrip("#").strip()  # Markdown-H1 ("# ") strippen
    hashtags = [w for w in lines[-1].split() if w.startswith("#")]
    description = "\n".join(lines[1:])
    tags = [h.lstrip("#") for h in hashtags]
    return title, description, tags


def prepare_thumbnail(path):
    """YouTube-Thumbnail-Limit: max 2 MB. Größere Dateien automatisch verkleinern."""
    if os.path.getsize(path) <= 2 * 1024 * 1024:
        return path
    print(f"Thumbnail {os.path.getsize(path) // 1024} KB > 2 MB — verkleinere...")
    from PIL import Image
    img = Image.open(path).convert("RGB")
    w, h = img.size
    if w > 1280:
        img = img.resize((1280, int(h * 1280 / w)), Image.LANCZOS)
    tmp = "/tmp/thumb_small.jpg"
    img.save(tmp, "JPEG", quality=85)
    print(f"  -> {os.path.getsize(tmp) // 1024} KB")
    return tmp


def upload(youtube, video_path, title, description, tags, privacy, thumbnail_path=None):
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": CATEGORY_MOTORCYCLE,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(video_path, resumable=True, chunksize=8 * 1024 * 1024)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    print("Upload gestartet...")
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            print(f"  {pct}%", end="\r", flush=True)
    print(f"  Fertig! {response['id']}")

    video_id = response["id"]
    if thumbnail_path:
        print("Thumbnail setzen...")
        thumb = prepare_thumbnail(thumbnail_path)
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(thumb),
        ).execute()
        print("  Thumbnail ok")
    return video_id


def main():
    parser = argparse.ArgumentParser(description="Lumi's Lane YouTube Uploader")
    parser.add_argument("--auth", action="store_true", help="einmalige OAuth-Autorisierung")
    parser.add_argument("video", nargs="?", help="Video-Datei")
    parser.add_argument("metadata", nargs="?", help="Metadaten-Datei (.md)")
    parser.add_argument("--thumb", default=None, help="Thumbnail-Bild (optional)")
    parser.add_argument("--privacy", default="private",
                        choices=["private", "unlisted", "public"], help="Sichtbarkeit")
    args = parser.parse_args()

    creds = get_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    if args.auth:
        # Nur Autorisierung testen: Kanal abfragen
        ch = youtube.channels().list(part="snippet", mine=True).execute()
        name = ch["items"][0]["snippet"]["title"]
        print(f"✅ Autorisiert als Kanal: {name}")
        return

    if not args.video or not args.metadata:
        parser.print_help()
        sys.exit(1)

    if not os.path.exists(args.video):
        sys.exit(f"FEHLER: Video nicht gefunden: {args.video}")
    if not os.path.exists(args.metadata):
        sys.exit(f"FEHLER: Metadaten nicht gefunden: {args.metadata}")

    title, description, tags = parse_metadata(args.metadata)
    print(f"Titel: {title}")
    print(f"Tags: {tags}")

    video_id = upload(youtube, args.video, title, description, tags,
                      args.privacy, args.thumb)
    print(f"\n🔗 https://youtu.be/{video_id}")


if __name__ == "__main__":
    main()
