import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ai_metadata


class FakeResponse:
    ok = True
    status_code = 200
    text = ""

    def __init__(self, content):
        self.content = content

    def json(self):
        return {"choices": [{"message": {"content": json.dumps(self.content)}}]}


class OllamaResponse:
    ok = True
    status_code = 200
    text = ""

    def __init__(self, content):
        self.content = content

    def json(self):
        return {"message": {"content": json.dumps(self.content)}}


class AIMetadataTests(unittest.TestCase):
    def test_reports_unconfigured_without_attempting_network(self):
        with patch.dict(os.environ, {}, clear=True):
            artifact = ai_metadata.generate_shadow_metadata("/not/used.mp4", {})
        self.assertEqual(artifact["status"], "not_configured")
        self.assertIn("AI_METADATA_BASE_URL", artifact["reason"])

    def test_generates_valid_platform_copy(self):
        config = {"provider": "openai", "endpoint": "https://example.invalid/v1", "model": "test", "api_key": "secret"}
        scene = {
            "scene_type": "rural curves", "road_character": "flowing bends",
            "environment": ["forest"], "light": "golden", "mood": ["free"],
            "visual_hooks": ["clear road"], "riding_moment": "smooth sequence",
            "claims_to_avoid": ["road name"], "confidence": 0.9,
        }
        result = {
            "hook_category": "road_first",
            "youtube": {"title": "POV: Curves That Keep Calling | GSX-8S Raw Sound",
                        "description": "A clean run.\n\n#Shorts #GSX8S #RawSound #BikerLife",
                        "tags": ["GSX8S", "raw sound"]},
            "tiktok": {"title": "POV: one more bend 🔊🏍️", "caption": "One more bend.\n\n#GSX8S #RawSound #fyp"},
            "instagram": {"caption": "One more bend.\n\n#GSX8S #RawSound #BikerLife"},
        }
        with patch("ai_metadata.requests.post", return_value=FakeResponse(result)) as post:
            output = ai_metadata.generate_copy(config, scene, {})
        self.assertEqual(output["hook_category"], "road_first")
        self.assertIn("#Shorts", output["youtube"]["description"])
        self.assertIn("#fyp", output["tiktok"]["caption"])
        request = post.call_args.kwargs
        self.assertEqual(request["headers"]["Authorization"], "Bearer secret")
        self.assertNotIn("secret", json.dumps(request["json"]))

    def test_ollama_uses_native_chat_and_no_authorization_header(self):
        config = {"provider": "ollama", "endpoint": "http://ollama", "model": "qwen3.5:9b"}
        with patch("ai_metadata.requests.post", return_value=OllamaResponse({"ok": True})) as post:
            output = ai_metadata._chat(config, [{"role": "user", "content": "Return JSON."}])
        self.assertEqual(output, {"ok": True})
        self.assertEqual(post.call_args.args[0], "http://ollama/api/chat")
        self.assertNotIn("Authorization", post.call_args.kwargs["headers"])
        self.assertTrue(post.call_args.kwargs["json"]["think"] is False)

    def test_writes_artifact_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "short.ai-metadata.json"
            ai_metadata.write_artifact(str(path), {"status": "generated", "copy": {}})
            self.assertEqual(json.loads(path.read_text())["status"], "generated")
            self.assertFalse(Path(str(path) + ".tmp").exists())


if __name__ == "__main__":
    unittest.main()
