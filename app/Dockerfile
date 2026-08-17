FROM python:3.12-slim

WORKDIR /app

# ffmpeg für Audio-Analyse + Video-Schnitt (Shorts-Pipeline)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/upload.py app/make_shorts.py app/api.py .

# /config: client_secret.json + token.json (OAuth — wird gemountet, NIE im Image)
# /material: 2_output (Quellvideos)
# /pipeline: state.json + out/ + shorts.log
VOLUME ["/config", "/material", "/pipeline"]

EXPOSE 8000

# Default: dauerlaufender HTTP-Service.
# One-Shot-Aufrufe (z.B. manuell):
#   docker run ... image python /app/upload.py /material/x.mp4 /pipeline/out/x.md --privacy private
#   docker run ... image python /app/make_shorts.py --dry-run --video x.mp4
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
