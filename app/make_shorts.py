#!/usr/bin/env python3
"""
Lumi's Lane Shorts-Pipeline
===========================
Analysiert Ride-Videos aus /material (2_output), schneidet die lautesten
("revviesten") Momente als vertikale Shorts (9:16, 1080x1920) und übergibt
sie als Drafts an Postiz für YouTube, TikTok und Instagram.

Läuft im Container (Service oder One-Shot):
  /material  -> 2_output (Quellvideos)
  /pipeline  -> state.json, out/, shorts.log
  /config    -> client_secret.json + token.json (OAuth)

CLI:  make_shorts.py [--dry-run] [--video <name>]
API:  wird von api.py als run_pipeline() importiert
"""
import argparse
import json
import os
import re
import subprocess
import tempfile
import wave
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from postiz_client import MediaAsset, PostizClient, PostizError
import ai_metadata

MATERIAL = "/material"
PIPELINE = "/pipeline"
OUT = os.path.join(PIPELINE, "out")
STATE = os.path.join(PIPELINE, "state.json")
LOG = os.path.join(PIPELINE, "shorts.log")
UPLOADER = "/app/upload.py"

EDGE = 5            # erste/letzte Sekunden ignorieren (Kamera-Hantieren)
SHORT_MIN = 20      # Segmentlänge min (s)
SHORT_MAX = 40      # Segmentlänge max (s)
MIN_GAP = 25        # Mindestabstand zwischen Segmenten (s)
SHORTS_PER_VIDEO = 3
TARGET_FPS = 30
MAX_LEN = 60        # Shorts-Hartlimit

# Optional: bekannte Quellnamen -> schönere Titel. Fallback: Dateiname aufbereitet.
NAME_MAP = {
    "kk_back": "Kalte Kuchl",
    "kuchl_crash": "Kalte Kuchl",
    "schoenau": "St. Georgen to Schönau",
    "wachau": "Wachau",
    "motorradsegnung": "Motorradsegnung",
    "grein": "Grein",
}

HASHTAGS = "#Shorts #GSX8S #SuzukiGSX8S #RawSound #MotoVlog #MotorcycleRide #ArrowsExhaust"

# Bike im Titel (bei Couple-Rides o.ä. anpassen)
BIKE_TITLE = "Suzuki GSX-8S + Arrow Exhaust"


def log(msg):
    line = f"[{__import__('datetime').datetime.now():%Y-%m-%d %H:%M}] {msg}"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} -> rc={r.returncode}: {r.stderr[-600:]}")
    return r.stdout


def probe(path):
    out = run(["ffprobe", "-v", "quiet", "-print_format", "json",
               "-show_format", "-show_streams", path])
    d = json.loads(out)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    dur = float(d["format"]["duration"])
    return int(v["width"]), int(v["height"]), dur


def extract_energy(path, dur):
    """RMS-Energie pro Sekunde aus dem Audiokanal (8 kHz mono)."""
    import numpy as np
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    try:
        run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "8000",
             "-f", "wav", "-y", tmp.name])
        with wave.open(tmp.name, "rb") as w:
            n, rate = w.getnframes(), w.getframerate()
            data = w.readframes(n)
        a = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        secs = int(dur)
        out = []
        for i in range(secs):
            chunk = a[i * rate:(i + 1) * rate]
            out.append(float(np.sqrt(np.mean(chunk ** 2))) if chunk.size else 0.0)
        return out
    finally:
        os.unlink(tmp.name)


def pick_segments(energies, dur):
    """Die SHORTS_PER_VIDEO lautesten, nicht-überlappenden Fenster (20-40s)."""
    lo, hi = EDGE, int(dur) - EDGE
    if hi - lo < SHORT_MIN + 4:
        return []
    cands = []
    for s in range(lo, hi - SHORT_MIN + 1):
        for w in (SHORT_MIN, 25, 30, 35, SHORT_MAX):
            if s + w > hi:
                continue
            cands.append((sum(energies[s:s + w]) / w, s, w))
    cands.sort(key=lambda x: (-x[0], -x[2]))
    picked = []
    for e, s, w in cands:
        ok = all(s >= p + pw + MIN_GAP or p >= s + w + MIN_GAP for p, pw in picked)
        if ok:
            picked.append((s, w))
        if len(picked) >= SHORTS_PER_VIDEO:
            break
    return picked


def make_short(src, out_path, start, width, height, seg_len):
    if width >= height:
        ch = height
        cw = int(ch * 9 / 16)
        if cw > width:
            cw = width
            ch = int(cw * 16 / 9)
        x = (width - cw) // 2
        y = (height - ch) // 2
        vf = (f"crop={cw}:{ch}:{x}:{y},"
              f"scale=1080:1920:force_original_aspect_ratio=decrease,"
              f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2,"
              f"fps={TARGET_FPS},format=yuv420p")
    else:
        vf = (f"scale=1080:1920:force_original_aspect_ratio=decrease,"
              f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2,"
              f"fps={TARGET_FPS},format=yuv420p")
    run(["ffmpeg", "-v", "error", "-ss", f"{start:.1f}", "-i", src,
         "-t", f"{seg_len:.1f}", "-vf", vf,
         "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
         "-movflags", "+faststart", "-y", out_path])


def nice_name(stem):
    base = re.sub(r"[-_.]+", "_", stem).strip("_").lower()
    for key, rep in NAME_MAP.items():
        if base.startswith(key) or key.startswith(base.split("_")[0]):
            return rep
    return base.replace("_", " ").title()


def make_metadata(video_label, seg_len, variant=0):
    titles = [
        "POV: The Road Was Too Good to Leave | GSX-8S Raw Sound",
        "POV: You Take the Long Way Home | GSX-8S Raw Sound",
        "POV: The Perfect Stretch of Road | GSX-8S Raw Sound",
    ]
    title = titles[variant % len(titles)]
    desc = (
        "POV: you find that one stretch of road and forget where you were headed. 🔊🏍️\n\n"
        "No music. No voiceover. Just raw GSX-8S + Arrow exhaust sound.\n\n"
        "Somewhere in Austria.\n\n"
        "Welcome to Lumi's Lane — where every ride tells a story."
    )
    return f"{title}\n\n{desc}\n\n{HASHTAGS}\n"


def parse_metadata(md_path):
    """Parse the existing metadata file without invoking the legacy uploader."""
    with open(md_path, encoding="utf-8") as f:
        lines = [line.rstrip() for line in f if line.strip()]
    if not lines:
        raise RuntimeError(f"Metadaten-Datei ist leer: {md_path}")
    title = lines[0].lstrip("#").strip()
    hashtags = [word for word in lines[-1].split() if word.startswith("#")]
    description = "\n".join(lines[1:])
    return title, description, [tag.lstrip("#") for tag in hashtags]


def load_state():
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE)


QUEUE_TZ = ZoneInfo("Europe/Vienna")
QUEUE_TIME = time(18, 30)
QUEUE_INTERVAL_DAYS = 2
SKIPPED_LEGACY_SOURCES = {"schoenau.mp4"}


def prepare_legacy_migration(state):
    """Label old direct YouTube records without creating or changing Postiz posts."""
    for video, vstate in state.items():
        if not isinstance(vstate, dict):
            continue
        if video.lower() in SKIPPED_LEGACY_SOURCES:
            vstate["migration"] = {"status": "skip", "reason": "Schönau already published"}
            continue
        for key, srec in vstate.items():
            if not key.startswith("short_") or not isinstance(srec, dict):
                continue
            if not srec.get("uploaded"):
                continue
            migration = srec.setdefault("migration", {})
            if migration.get("status") in {"scheduled", "published", "skip"}:
                continue
            migration["status"] = "ready"
            if srec.get("url"):
                migration.setdefault("legacy_youtube_url", srec["url"])
    return state


def plan_queue_slots(count, now=None):
    """Return deterministic 18:30 Vienna slots, two calendar days apart."""
    if count < 0:
        raise ValueError("count must not be negative")
    now = now or datetime.now(QUEUE_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=QUEUE_TZ)
    else:
        now = now.astimezone(QUEUE_TZ)
    first = datetime.combine(now.date(), QUEUE_TIME, tzinfo=QUEUE_TZ)
    if now >= first:
        first += timedelta(days=1)
    return [first + timedelta(days=QUEUE_INTERVAL_DAYS * i) for i in range(count)]


def legacy_queue_candidates(state, now=None):
    """Return only migration-ready legacy Shorts, excluding explicitly skipped rides."""
    candidates = []
    for video in sorted(state):
        vstate = state[video]
        if not isinstance(vstate, dict) or vstate.get("migration", {}).get("status") == "skip":
            continue
        stem = os.path.splitext(video)[0]
        for key in sorted(k for k in vstate if k.startswith("short_")):
            srec = vstate[key]
            if not isinstance(srec, dict) or srec.get("migration", {}).get("status") != "ready":
                continue
            candidates.append({
                "video": video, "key": key,
                "mp4": os.path.join(OUT, stem, f"{key}.mp4"),
                "metadata": os.path.join(OUT, stem, f"{key}.md"),
            })
    for candidate, slot in zip(candidates, plan_queue_slots(len(candidates), now=now)):
        candidate["scheduled_at"] = slot.isoformat().replace("+00:00", "Z")
    return candidates


def migrate_legacy_queue(apply=False, now=None):
    """Plan, or deliberately create, the historical two-day Postiz queue.

    `apply=False` is side-effect-free. With apply enabled, every external boundary is
    persisted first so a failed run cannot silently create duplicate calendar items.
    """
    state = load_state()
    prepare_legacy_migration(state)
    candidates = legacy_queue_candidates(state, now=now)
    if not apply:
        return {"apply": False, "count": len(candidates), "candidates": candidates}

    save_state(state)  # Persist Schönau skip + ready labels before touching Postiz.
    client = PostizClient()
    scheduled = []
    for candidate in candidates:
        srec = state[candidate["video"]][candidate["key"]]
        migration = srec["migration"]
        if migration.get("status") != "ready":
            continue
        if not os.path.isfile(candidate["mp4"]) or not os.path.isfile(candidate["metadata"]):
            migration.update({"status": "failed", "error": "render or metadata missing"})
            save_state(state)
            continue
        try:
            postiz = srec.setdefault("postiz", {})
            if postiz.get("media_id") and postiz.get("media_path"):
                media = MediaAsset(postiz["media_id"], postiz["media_path"])
            else:
                media = client.upload_media(candidate["mp4"])
                postiz.update({"media_id": media.id, "media_path": media.path})
                save_state(state)
            title, description, tags = parse_metadata(candidate["metadata"])
            platform_copy = srec.get("ai_metadata", {}).get("copy")
            migration.update({"status": "scheduling", "scheduled_at": candidate["scheduled_at"]})
            save_state(state)
            response = client.create_scheduled_posts(
                media, title, description, tags, candidate["scheduled_at"], platform_copy=platform_copy
            )
            migration.update({"status": "scheduled", "postiz_response": response})
            scheduled.append(candidate)
            save_state(state)
        except Exception as exc:
            # 'scheduling' intentionally remains ambiguous: never retry blindly.
            migration["error"] = str(exc)
            save_state(state)
            raise
    return {"apply": True, "count": len(scheduled), "scheduled": scheduled}


def run_pipeline(video=None, dry_run=False):
    """Hauptlogik — wird von CLI und API genutzt. Gibt Summary-Dict zurück."""
    os.makedirs(OUT, exist_ok=True)
    state = load_state()

    videos = sorted(
        f for f in os.listdir(MATERIAL)
        if f.endswith(".mp4") and os.path.isfile(os.path.join(MATERIAL, f))
    )
    if video:
        videos = [v for v in videos if v == video or video in v]
    videos = [v for v in videos if not state.get(v, {}).get("done")]

    summary = {"dry_run": dry_run, "processed": []}
    if not videos:
        return summary
    if not dry_run:
        # Fail before expensive ffmpeg work when the deployment secret is absent.
        PostizClient()

    for name in videos:
        src = os.path.join(MATERIAL, name)
        stem = os.path.splitext(name)[0]
        label = nice_name(stem)
        rec = {"video": name, "label": label, "shorts": []}
        log(f"=== {name} ({label}) ===")
        try:
            width, height, dur = probe(src)
            log(f"Quelle: {width}x{height}, {dur:.0f}s")
            energies = extract_energy(src, dur)
            segs = pick_segments(energies, dur)
            if not segs:
                log(f"SKIP {name}: kein brauchbares Segment (zu kurz/still)")
                rec["status"] = "skipped"
                summary["processed"].append(rec)
                continue
            vstate = state.setdefault(name, {})
            made = 0
            for i, (start, seg_len) in enumerate(segs, 1):
                key = f"short_{i}"
                srec = vstate.get(key) or {}
                # Bereits direkt zu YouTube hochgeladene Legacy-Shorts nie duplizieren.
                if srec.get("uploaded"):
                    log(f"  {key} historisch direkt hochgeladen ({srec.get('url')}) — übersprungen")
                    rec["shorts"].append({"key": key, "url": srec.get("url"), "legacy": True})
                    continue
                if srec.get("postiz_draft_created"):
                    log(f"  {key} bereits als Postiz-Draft übergeben — übersprungen")
                    rec["shorts"].append({"key": key, "postiz": srec.get("postiz")})
                    continue
                out_dir = os.path.join(OUT, stem)
                os.makedirs(out_dir, exist_ok=True)
                mp4 = os.path.join(out_dir, f"{key}.mp4")
                md = os.path.join(out_dir, f"{key}.md")
                if not os.path.exists(mp4):
                    log(f"  Schneide {key}: ab {start}s, {seg_len}s ...")
                    make_short(src, mp4, start, width, height, seg_len)
                    with open(md, "w", encoding="utf-8") as f:
                        f.write(make_metadata(label, seg_len, i - 1))
                log(f"  {key}: {mp4} ({os.path.getsize(mp4)/1e6:.1f} MB)")
                # Shadow mode: write a reviewable AI artifact, but keep the existing
                # template metadata for Postiz until Thomas explicitly approves phase B.
                if not dry_run and not srec.get("ai_metadata"):
                    try:
                        artifact = ai_metadata.generate_shadow_metadata(mp4, state)
                        artifact_path = f"{mp4[:-4]}.ai-metadata.json"
                        if artifact["status"] == "generated":
                            ai_metadata.write_artifact(artifact_path, artifact)
                            log(f"  🤖 KI-Schattenanalyse erstellt: {artifact_path}")
                        else:
                            log(f"  KI-Schattenanalyse übersprungen: {artifact['reason']}")
                        artifact["path"] = artifact_path if artifact["status"] == "generated" else None
                        srec["ai_metadata"] = artifact
                        vstate[key] = srec
                        save_state(state)
                    except Exception as exc:
                        # AI generation is never allowed to block rendering or draft creation.
                        log(f"  WARN KI-Schattenanalyse {key}: {exc}")
                        srec["ai_metadata"] = {"status": "error", "error": str(exc),
                                               "version": ai_metadata.AI_METADATA_VERSION}
                        vstate[key] = srec
                        save_state(state)
                if dry_run:
                    srec["start"], srec["len"] = start, seg_len
                    srec["dry_run"] = True
                    vstate[key] = srec
                    rec["shorts"].append({"key": key, "start": start, "len": seg_len,
                                          "dry_run": True})
                    continue
                # Persist every external boundary. A run interrupted after a media upload
                # reuses that asset; a lost draft response is held for manual reconciliation
                # rather than risking three duplicate drafts on the next cron tick.
                try:
                    postiz = srec.get("postiz", {})
                    if postiz.get("status") == "draft_creating":
                        raise PostizError("previous draft request is ambiguous; reconcile this short in Postiz before retrying")
                    client = PostizClient()
                    if postiz.get("media_id") and postiz.get("media_path"):
                        media = MediaAsset(postiz["media_id"], postiz["media_path"])
                    else:
                        media = client.upload_media(mp4)
                        postiz.update({"media_id": media.id, "media_path": media.path, "status": "media_uploaded"})
                        srec["postiz"] = postiz
                        vstate[key] = srec
                        save_state(state)
                    title, description, tags = parse_metadata(md)
                    ai = srec.get("ai_metadata", {})
                    platform_copy = ai.get("copy") if ai.get("status") == "generated" else None
                    if platform_copy:
                        try:
                            for name in ("youtube", "tiktok", "instagram"):
                                if not isinstance(platform_copy[name], dict):
                                    raise KeyError(name)
                            postiz["metadata_source"] = "ai_shadow"
                        except (KeyError, TypeError):
                            platform_copy = None
                    if not platform_copy:
                        postiz["metadata_source"] = "template_fallback"
                    postiz["status"] = "draft_creating"
                    srec["postiz"] = postiz
                    vstate[key] = srec
                    save_state(state)
                    drafts = client.create_drafts(media, title, description, tags, platform_copy=platform_copy)
                except PostizError as exc:
                    log(f"  FEHLER Postiz-Übergabe {key}: {exc}")
                    rec["shorts"].append({"key": key, "error": str(exc)})
                    save_state(state)
                    summary["processed"].append(rec)
                    return summary
                srec["start"], srec["len"] = start, seg_len
                srec["postiz_draft_created"] = True
                postiz.update({"status": "draft_created", "draft_response": drafts})
                srec["postiz"] = postiz
                vstate[key] = srec
                rec["shorts"].append({"key": key, "postiz": srec["postiz"]})
                log(f"  ✅ {key} als Draft an Postiz übergeben: {media.path}")
                made += 1
            if not dry_run:
                vstate["done"] = True
                log(f"DONE {name} — {made} neue Shorts (privat)")
            else:
                log(f"DRY-RUN {name} — {len(segs)} Segmente erstellt, kein Upload")
            rec["status"] = "done"
            save_state(state)
        except Exception as e:
            log(f"FEHLER bei {name}: {e}")
            rec["status"] = "error"
            rec["error"] = str(e)
            save_state(state)
        summary["processed"].append(rec)
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="analysieren + schneiden, aber NICHT hochladen")
    ap.add_argument("--video", help="nur dieses Video verarbeiten (Dateiname)")
    args = ap.parse_args()
    run_pipeline(video=args.video, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
