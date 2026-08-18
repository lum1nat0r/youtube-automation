# Lumi's Lane YouTube Automation

Automatisierte YouTube-Pipeline für den Kanal **Lumi's Lane** (Suzuki GSX-8S,
raw sound rides):

- **Uploader** (`app/upload.py`) — YouTube-Data-API-Upload mit Metadaten-Datei
  (Zeile 1 = Titel, letzte Zeile = Hashtags, Rest = Beschreibung), OAuth via
  `/config`-Mount (client_secret.json + token.json)
- **Shorts-Pipeline** (`app/make_shorts.py`) — analysiert Ride-Videos aus
  `/material`, schneidet die lautesten 20–40s-Momente als vertikale Shorts
  (9:16, 1080×1920, loudnorm) und legt sie als **Postiz-Drafts** für YouTube,
  TikTok und Instagram an
- **HTTP-Service** (`app/api.py`, FastAPI) — dauerlaufender Container, der für
  Uploads/Shorts invoked wird

Keine Credentials im Repo — OAuth-Token, Client-Secret und der Postiz-API-Key
werden ausschließlich über den Volume-Mount `/config` bereitgestellt. Der Service
ist per compose nur an `127.0.0.1` gebunden.

### Postiz-Key für Shorts

In Postiz unter **Settings → Developers** einen API-Key erzeugen und auf apollo
als `/mnt/user/appdata/youtube-uploader/config/postiz_api_key` ablegen
(`chmod 600`). Der Key wird nicht geloggt. Ohne diese Datei rendert die Pipeline
keine neuen Drafts und gibt stattdessen eine eindeutige Fehlermeldung aus.

### KI-Schattenanalyse für Shorts

Die KI-Analyse ist bewusst zunächst nur ein **Review-Schritt**: Sie erstellt
neben jedem analysierten Short eine `<short>.ai-metadata.json` mit Szenenprofil,
YouTube-/TikTok-/Instagram-Copy und einem Hook-Kategorietag. Sie verändert weder
die bestehende `.md`-Metadaten-Datei noch Postiz-Drafts oder Veröffentlichungen.

Konfiguration auf apollo:

- Standard: lokales Ollama über `http://host.docker.internal:11434` mit
  `qwen3.5:9b`; kein API-Key und kein externer Dienst erforderlich.
- Der Compose-Stack setzt `AI_METADATA_PROVIDER=ollama` sowie das sichere
  Docker-Host-Gateway automatisch.
- Voraussetzung: ComfyUI muss seinen VRAM nach Idle freigeben; der installierte
  watchdog erledigt das nach 15 Minuten, damit der Vision-Request genug GPU-RAM
  bekommt.

Ist Ollama nicht erreichbar oder die KI temporär nicht verfügbar,
läuft die bestehende Shorts-/Postiz-Pipeline unverändert weiter. Das Ereignis wird
nur als `not_configured` bzw. `error` im Pipeline-State protokolliert.

Bestehenden Short ohne Postiz-Nebenwirkung analysieren:

```bash
curl -s -X POST http://localhost:8082/ai-metadata \
  -H 'content-type: application/json' \
  -d '{"short":"/pipeline/out/schoenau/short_2.mp4"}'
```

## Struktur

```
app/
  upload.py        # Uploader (CLI + Modul)
  make_shorts.py   # Shorts-Pipeline (CLI + Modul)
  api.py           # FastAPI-Service
  Dockerfile
  requirements.txt
compose.yaml       # Portainer/docker-compose-Stack
.github/workflows/ci.yml  # Build + Push nach GHCR (keine Secrets nötig)
```

## Deployment (Portainer)

1. Image wird automatisch per GitHub Actions gebaut →
   `ghcr.io/lum1nat0r/youtube-automation:latest`
2. Portainer → **Stacks → Add stack → Repository**:
   - URL: `https://github.com/lum1nat0r/youtube-automation`
   - Branch: `main`, Compose-Pfad: `compose.yaml`
3. Stack „yt-service" starten. Mounts (müssen auf dem Host existieren):
   - `/mnt/user/video-material/Lumis-Lane/2_output` → `/material`
   - `/mnt/user/appdata/shorts-pipeline` → `/pipeline` (state, out, logs)
   - `/mnt/user/appdata/youtube-uploader/config` → `/config` (OAuth-Token)

## Aufruf

```bash
# Service (läuft dauerhaft, Port 127.0.0.1:8080)
curl -s http://localhost:8082/health
curl -s -X POST http://localhost:8082/shorts            # neue Videos -> Shorts (privat)
curl -s -X POST http://localhost:8082/shorts -H 'content-type: application/json' \
     -d '{"video":"schoenau.mp4","dry_run":true}'
curl -s -X POST http://localhost:8080/upload -H 'content-type: application/json' \
     -d '{"video":"/material/x.mp4","metadata":"/pipeline/out/x/x.md"}'

# One-Shot (ohne Service):
docker run --rm -v ...:/material -v ...:/pipeline -v ...:/config \
  ghcr.io/lum1nat0r/youtube-automation:latest python /app/make_shorts.py --dry-run
```

## Manueller Upload (alter Flow)

```bash
docker run --rm \
  -v /mnt/user/appdata/youtube-uploader/config:/config \
  -v /mnt/user/appdata/youtube-uploader/uploads:/uploads \
  ghcr.io/lum1nat0r/youtube-automation:latest \
  python /app/upload.py /uploads/video.mp4 /uploads/meta.md --privacy private --thumb /uploads/t.jpg
```
