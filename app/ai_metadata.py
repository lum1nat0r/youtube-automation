"""Shadow-mode AI analysis and social metadata for Lumi's Lane Shorts.

The module never creates Postiz posts. It extracts representative frames from a
rendered short, asks an OpenAI-compatible multimodal endpoint for factual scene
facts, then asks for platform-specific copy. Results are stored next to the
short and can be reviewed before being wired into draft creation.
"""
import base64
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests

AI_METADATA_VERSION = "2026-08-18-shadow-v1"
FRAME_POSITIONS = (0.05, 0.18, 0.33, 0.50, 0.67, 0.82, 0.95)
MAX_RECENT_ITEMS = 24


class AIMetadataError(RuntimeError):
    pass


def _run(command):
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise AIMetadataError(result.stderr[-600:] or "frame extraction failed")
    return result.stdout


def _env_config():
    """Return validated config, or (None, reason) when shadow mode is disabled."""
    endpoint = os.getenv("AI_METADATA_BASE_URL", "").rstrip("/")
    model = os.getenv("AI_METADATA_MODEL", "").strip()
    key_file = os.getenv("AI_METADATA_API_KEY_FILE", "/config/ai_metadata_api_key")
    if not endpoint or not model:
        return None, "AI_METADATA_BASE_URL or AI_METADATA_MODEL is not configured"
    try:
        api_key = Path(key_file).read_text(encoding="utf-8").strip()
    except OSError:
        return None, f"API key file is unavailable: {key_file}"
    if not api_key:
        return None, f"API key file is empty: {key_file}"
    return {"endpoint": endpoint, "model": model, "api_key": api_key}, None


def configured():
    config, reason = _env_config()
    return config is not None, reason


def probe_duration(video_path):
    data = json.loads(_run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", video_path,
    ]))
    return float(data["format"]["duration"])


def extract_frames(video_path, directory):
    """Extract a small, ordered set of JPEG frames without retaining them."""
    duration = probe_duration(video_path)
    frames = []
    for index, position in enumerate(FRAME_POSITIONS, 1):
        timestamp = max(0.0, min(duration - 0.05, duration * position))
        frame = os.path.join(directory, f"frame-{index:02d}.jpg")
        _run([
            "ffmpeg", "-v", "error", "-ss", f"{timestamp:.3f}", "-i", video_path,
            "-frames:v", "1", "-vf", "scale=768:-2", "-q:v", "4", "-y", frame,
        ])
        frames.append({"timestamp_seconds": round(timestamp, 2), "path": frame})
    return frames


def _image_message(frame):
    encoded = base64.b64encode(Path(frame["path"]).read_bytes()).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{encoded}", "detail": "low"},
    }


def _parse_json(content):
    if not isinstance(content, str):
        raise AIMetadataError("model response did not contain text")
    clean = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", content.strip(), flags=re.I)
    try:
        return json.loads(clean)
    except json.JSONDecodeError as exc:
        raise AIMetadataError(f"model returned invalid JSON: {exc}") from exc


def _chat(config, messages):
    response = requests.post(
        f"{config['endpoint']}/chat/completions",
        headers={"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"},
        json={"model": config["model"], "temperature": 0.55, "response_format": {"type": "json_object"}, "messages": messages},
        timeout=90,
    )
    if not response.ok:
        raise AIMetadataError(f"AI endpoint HTTP {response.status_code}: {response.text[-400:]}")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AIMetadataError("unexpected AI endpoint response") from exc
    return _parse_json(content)


def analyze_scene(config, frames):
    prompt = """You analyze a short raw motorcycle-ride video for factual social metadata.
Use only what is visibly supported by the supplied ordered frames. Do not identify an exact road, location, speed, weather fact, rider identity, or bike model unless unmistakably visible. The channel is Lumi's Lane, featuring a Suzuki GSX-8S with raw exhaust sound.
Return JSON only with exactly these keys:
scene_type, road_character, environment, light, mood, visual_hooks, riding_moment, claims_to_avoid, confidence.
Values: scene_type/road_character/light/riding_moment are short strings; environment/mood/visual_hooks/claims_to_avoid are arrays of short strings; confidence is a number 0 to 1."""
    content = [{"type": "text", "text": prompt}]
    content.extend(_image_message(frame) for frame in frames)
    profile = _chat(config, [{"role": "user", "content": content}])
    required = {"scene_type", "road_character", "environment", "light", "mood", "visual_hooks", "riding_moment", "claims_to_avoid", "confidence"}
    missing = required - profile.keys()
    if missing:
        raise AIMetadataError(f"scene profile is missing fields: {', '.join(sorted(missing))}")
    return profile


def _recent_copy(state):
    entries = []
    for video in state.values():
        if not isinstance(video, dict):
            continue
        for short in video.values():
            if not isinstance(short, dict):
                continue
            ai = short.get("ai_metadata", {})
            copy = ai.get("copy") if isinstance(ai, dict) else None
            if isinstance(copy, dict):
                entries.append(copy)
    return entries[-MAX_RECENT_ITEMS:]


def generate_copy(config, scene_profile, state):
    recent = _recent_copy(state)
    prompt = f"""Create fresh, trendy English social copy for one Lumi's Lane raw motorcycle short.

Scene facts (do not add claims outside these facts):
{json.dumps(scene_profile, ensure_ascii=False)}

Use a distinct hook and wording from these recently used outputs:
{json.dumps(recent, ensure_ascii=False)}

Rules:
- No exact route, road name, speed, weather claim, or invented event.
- Generic location is allowed: 'Somewhere in Austria.'
- The motorcycle is a Suzuki GSX-8S with Arrow exhaust.
- Voice: concise, natural, emotional, POV/hook-oriented. No generic marketing CTA.
- YouTube title must include 'GSX-8S' and 'Raw Sound', be <= 100 chars, and differ materially from recent titles.
- YouTube description must contain #Shorts and 4-7 targeted hashtags total.
- TikTok caption: 1-3 short paragraphs and 5-7 hashtags, including #fyp.
- Instagram caption: 1-3 short paragraphs and 5-7 hashtags, never #fyp or #Shorts.

Return JSON only:
{{
  "hook_category": "one of POV|sound_first|road_first|emotional_observation|minimalist",
  "youtube": {{"title": "...", "description": "...", "tags": ["..."]}},
  "tiktok": {{"title": "...", "caption": "..."}},
  "instagram": {{"caption": "..."}}
}}"""
    copy = _chat(config, [{"role": "user", "content": prompt}])
    for key in ("hook_category", "youtube", "tiktok", "instagram"):
        if key not in copy:
            raise AIMetadataError(f"copy output is missing {key}")
    yt = copy["youtube"]
    if not all(key in yt for key in ("title", "description", "tags")):
        raise AIMetadataError("YouTube output is incomplete")
    if len(yt["title"]) > 100 or "GSX-8S" not in yt["title"] or "Raw Sound" not in yt["title"]:
        raise AIMetadataError("YouTube title violates channel rules")
    if "#Shorts" not in yt["description"]:
        raise AIMetadataError("YouTube description is missing #Shorts")
    return copy


def generate_shadow_metadata(video_path, state):
    """Return a review artifact; never changes Postiz payloads or creates posts."""
    config, reason = _env_config()
    if config is None:
        return {"status": "not_configured", "reason": reason, "version": AI_METADATA_VERSION}
    with tempfile.TemporaryDirectory(prefix="lumi-short-frames-") as directory:
        frames = extract_frames(video_path, directory)
        scene_profile = analyze_scene(config, frames)
    copy = generate_copy(config, scene_profile, state)
    return {
        "status": "generated",
        "version": AI_METADATA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "frame_timestamps_seconds": [frame["timestamp_seconds"] for frame in frames],
        "scene_profile": scene_profile,
        "copy": copy,
    }


def write_artifact(path, artifact):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(artifact, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
