#!/usr/bin/env python3
"""
Lumi's Lane Shorts-Pipeline
===========================
Analysiert Ride-Videos aus /material (2_output), schneidet die lautesten
("revviesten") Momente als vertikale Shorts (9:16, 1080x1920) und lädt sie
privat auf YouTube hoch (upload.py + OAuth-Token aus /config).

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
import sys
import tempfile
import wave

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


def make_metadata(video_label, seg_len):
    title = (f"{BIKE_TITLE} – {video_label} – {seg_len}s of Pure Sound | Raw Sound")
    desc = (
        "Raw sound. No music. No voiceover. Just the GSX-8S and the Arrows "
        f"exhaust — {seg_len} seconds of the best part of the ride.\n\n"
        f"📍 Route: {video_label}\n"
        "🏍️ Bike: Suzuki GSX-8S (Skye)\n"
        "🔊 Exhaust: Arrows Performance System\n"
        "🎬 Footage: 4K, raw motorcycle sound\n\n"
        "Welcome to Lumi's Lane — where every ride tells a story. "
        "Subscribe for more rides, routes, and raw two-wheeled experiences."
    )
    return f"{title}\n\n{desc}\n\n{HASHTAGS}\n"


def load_state():
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, ensure_ascii=False)


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
                if srec.get("uploaded"):
                    log(f"  {key} bereits hochgeladen ({srec.get('url')}) — übersprungen")
                    rec["shorts"].append({"key": key, "url": srec.get("url")})
                    continue
                out_dir = os.path.join(OUT, stem)
                os.makedirs(out_dir, exist_ok=True)
                mp4 = os.path.join(out_dir, f"{key}.mp4")
                md = os.path.join(out_dir, f"{key}.md")
                if not os.path.exists(mp4):
                    log(f"  Schneide {key}: ab {start}s, {seg_len}s ...")
                    make_short(src, mp4, start, width, height, seg_len)
                    with open(md, "w", encoding="utf-8") as f:
                        f.write(make_metadata(label, seg_len))
                log(f"  {key}: {mp4} ({os.path.getsize(mp4)/1e6:.1f} MB)")
                if dry_run:
                    srec["start"], srec["len"] = start, seg_len
                    srec["dry_run"] = True
                    vstate[key] = srec
                    rec["shorts"].append({"key": key, "start": start, "len": seg_len,
                                          "dry_run": True})
                    continue
                # Upload (privat) über den bestehenden Uploader
                up = subprocess.run(
                    [sys.executable, UPLOADER, mp4, md, "--privacy", "private"],
                    capture_output=True, text=True)
                out_txt = up.stdout.strip()
                if up.returncode != 0:
                    log(f"  FEHLER Upload {key}: {up.stderr[-400:]}")
                    rec["shorts"].append({"key": key, "error": up.stderr[-300:]})
                    save_state(state)
                    summary["processed"].append(rec)
                    return summary  # Video bleibt unfertig; nächster Lauf versucht Rest
                url = ""
                m = re.search(r"https://youtu\.be/[A-Za-z0-9_-]+", out_txt)
                if m:
                    url = m.group(0)
                log(f"  ✅ {key} hochgeladen (privat): {url}")
                srec["start"], srec["len"] = start, seg_len
                srec["uploaded"] = True
                srec["url"] = url
                vstate[key] = srec
                rec["shorts"].append({"key": key, "url": url})
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
