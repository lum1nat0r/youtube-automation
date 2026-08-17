"""
Lumi's Lane YouTube Automation — HTTP-Service.
Läuft dauerhaft im Container (Portainer/docker-compose) und wird für
Uploads und die Shorts-Pipeline invoked.

Endpunkte:
  GET  /health          -> Status
  GET  /status          -> State-Zusammenfassung (state.json)
  POST /shorts          -> Shorts-Pipeline ausführen   {"video"?, "dry_run"?}
  POST /upload          -> Manueller Upload             {"video", "metadata", "privacy"?, "thumb"?}

Alle Pfade sind Container-Pfade (/material/..., /pipeline/..., /config/...).
Kein API-Token nötig: Der Port ist per compose nur an 127.0.0.1 gebunden.
"""
import os
import threading
import traceback

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import make_shorts
import upload as up

app = FastAPI(title="Lumi's Lane YouTube Automation", version="1.0.0")
_lock = threading.Lock()


class ShortsReq(BaseModel):
    video: str | None = None
    dry_run: bool = False


class UploadReq(BaseModel):
    video: str
    metadata: str
    privacy: str = "private"
    thumb: str | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/status")
def status():
    return {"state": make_shorts.load_state()}


@app.post("/shorts")
def run_shorts(req: ShortsReq | None = None):
    # Body optional: ein nackter POST ohne Body verarbeitet alle neuen Videos
    if req is None:
        req = ShortsReq()
    if not _lock.acquire(blocking=False):
        raise HTTPException(409, "Shorts-Pipeline läuft bereits")
    try:
        return make_shorts.run_pipeline(video=req.video, dry_run=req.dry_run)
    except Exception:
        raise HTTPException(500, traceback.format_exc())
    finally:
        _lock.release()


@app.post("/upload")
def upload(req: UploadReq):
    if not os.path.exists(req.video):
        raise HTTPException(404, f"Video nicht gefunden: {req.video}")
    if not os.path.exists(req.metadata):
        raise HTTPException(404, f"Metadaten nicht gefunden: {req.metadata}")
    try:
        creds = up.get_credentials()
        yt = up.build("youtube", "v3", credentials=creds)
        title, desc, tags = up.parse_metadata(req.metadata)
        video_id = up.upload(yt, req.video, title, desc, tags, req.privacy, req.thumb)
        return {"video_id": video_id, "url": f"https://youtu.be/{video_id}"}
    except Exception:
        raise HTTPException(500, traceback.format_exc())
