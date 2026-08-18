"""Small, idempotent client for Postiz' self-hosted Public API."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

DEFAULT_BASE_URL = "https://postiz.tail.tlumesberger.at/api/public/v1"
DEFAULT_KEY_FILE = "/config/postiz_api_key"


class PostizError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaAsset:
    id: str
    path: str


class PostizClient:
    def __init__(self, base_url: str | None = None, api_key_file: str | None = None,
                 session: requests.Session | None = None):
        self.base_url = (base_url or os.environ.get("POSTIZ_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or parsed.hostname != "postiz.tail.tlumesberger.at":
            raise PostizError("POSTIZ_BASE_URL must be https://postiz.tail.tlumesberger.at/api/public/v1")
        self.api_key_file = api_key_file or os.environ.get("POSTIZ_API_KEY_FILE") or DEFAULT_KEY_FILE
        try:
            self.api_key = Path(self.api_key_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise PostizError(f"Postiz API key missing: {self.api_key_file}") from exc
        if not self.api_key:
            raise PostizError(f"Postiz API key is empty: {self.api_key_file}")
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": self.api_key})

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self.session.request(method, f"{self.base_url}{path}", timeout=(10, 180), **kwargs)
        except requests.RequestException as exc:
            raise PostizError(f"Postiz request failed: {exc}") from exc
        if not response.ok:
            raise PostizError(f"Postiz {method} {path} returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise PostizError(f"Postiz {method} {path} returned invalid JSON") from exc

    def integrations_by_provider(self) -> dict[str, dict[str, Any]]:
        data = self._request("GET", "/integrations")
        items = data.get("integrations", data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise PostizError("Unexpected integrations response")
        found = {item.get("providerIdentifier"): item for item in items if item.get("providerIdentifier")}
        required = {"youtube", "tiktok", "instagram-standalone"}
        missing = sorted(required - found.keys())
        if missing:
            raise PostizError("Postiz integrations missing: " + ", ".join(missing))
        return found

    def upload_media(self, path: str) -> MediaAsset:
        source = Path(path)
        if not source.is_file():
            raise PostizError(f"Short file missing: {path}")
        with source.open("rb") as handle:
            data = self._request("POST", "/upload", files={"file": (source.name, handle, "video/mp4")})
        if not isinstance(data, dict) or not data.get("id") or not data.get("path"):
            raise PostizError("Postiz upload response has no media id/path")
        return MediaAsset(id=data["id"], path=data["path"])

    def create_drafts(self, media: MediaAsset, title: str, description: str, tags: list[str]) -> Any:
        integrations = self.integrations_by_provider()
        tag_objects = [{"value": tag, "label": tag} for tag in tags]
        attachment = [{"id": media.id, "path": media.path}]
        common_value = [{"content": description, "image": attachment}]
        payload = {
            "type": "draft",
            "shortLink": False,
            "tags": [],
            "posts": [
                {
                    "integration": {"id": integrations["youtube"]["id"]},
                    "value": common_value,
                    "settings": {
                        "__type": "youtube", "title": title, "type": "private",
                        "selfDeclaredMadeForKids": "no", "tags": tag_objects,
                    },
                },
                {
                    "integration": {"id": integrations["tiktok"]["id"]},
                    "value": common_value,
                    "settings": {
                        "__type": "tiktok", "title": title[:90], "privacy_level": "SELF_ONLY",
                        "duet": True, "stitch": True, "comment": True, "autoAddMusic": "no",
                        "brand_content_toggle": False, "brand_organic_toggle": False,
                        "video_made_with_ai": False, "content_posting_method": "UPLOAD",
                    },
                },
                {
                    "integration": {"id": integrations["instagram-standalone"]["id"]},
                    "value": common_value,
                    "settings": {
                        "__type": "instagram-standalone", "post_type": "reel",
                        "is_trial_reel": False, "collaborators": [],
                    },
                },
            ],
        }
        return self._request("POST", "/posts", json=payload)
