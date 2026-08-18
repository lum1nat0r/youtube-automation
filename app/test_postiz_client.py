import tempfile
import unittest
from pathlib import Path

from postiz_client import MediaAsset, PostizClient


class FakeResponse:
    ok = True
    status_code = 200
    text = ""

    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


class FakeSession:
    def __init__(self):
        self.headers = {}
        self.calls = []

    def request(self, method, url, timeout, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("/integrations"):
            return FakeResponse({"integrations": [
                {"id": "yt", "providerIdentifier": "youtube"},
                {"id": "tt", "providerIdentifier": "tiktok"},
                {"id": "ig", "providerIdentifier": "instagram-standalone"},
            ]})
        if url.endswith("/posts"):
            return FakeResponse({"posts": [{"id": "draft-1"}]})
        raise AssertionError(url)


class PostizPayloadTests(unittest.TestCase):
    def test_creates_three_platform_drafts_with_private_youtube(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / "key"
            key.write_text("test-key")
            session = FakeSession()
            client = PostizClient("https://postiz.tail.tlumesberger.at/api/public/v1", str(key), session)
            client.create_drafts(
                MediaAsset("media-1", "https://media.example/short.mp4"),
                "A Short", "Description #Shorts", ["Shorts", "GSX8S"],
            )
        self.assertEqual(session.headers["Authorization"], "test-key")
        post_payload = session.calls[-1][2]["json"]
        self.assertEqual(post_payload["type"], "draft")
        self.assertTrue(post_payload["date"].endswith("Z"))
        self.assertEqual(len(post_payload["posts"]), 3)
        youtube, tiktok, instagram = post_payload["posts"]
        self.assertEqual(youtube["settings"]["type"], "private")
        self.assertEqual(tiktok["settings"]["content_posting_method"], "UPLOAD")
        self.assertEqual(tiktok["settings"]["privacy_level"], "SELF_ONLY")
        self.assertEqual(instagram["settings"]["post_type"], "reel")
        for item in post_payload["posts"]:
            self.assertEqual(item["value"][0]["image"][0]["path"], "https://media.example/short.mp4")
    def test_rejects_an_untrusted_postiz_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / "key"
            key.write_text("test-key")
            with self.assertRaisesRegex(Exception, "POSTIZ_BASE_URL"):
                PostizClient("https://example.invalid/api/public/v1", str(key), FakeSession())


if __name__ == "__main__":
    unittest.main()
