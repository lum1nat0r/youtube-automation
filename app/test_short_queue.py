import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import make_shorts


class ShortQueueTests(unittest.TestCase):
    def test_migration_skips_schoenau_and_releases_legacy_shorts(self):
        state = {
            "schoenau.mp4": {"short_1": {"uploaded": True, "url": "https://youtu.be/old"}, "done": True},
            "KK_back_1.mp4": {"short_1": {"uploaded": True, "url": "https://youtu.be/old"}, "done": True},
        }
        result = make_shorts.prepare_legacy_migration(state)
        self.assertEqual(result["schoenau.mp4"]["migration"]["status"], "skip")
        self.assertEqual(result["KK_back_1.mp4"]["short_1"]["migration"]["status"], "ready")
        self.assertEqual(result["KK_back_1.mp4"]["short_1"]["migration"]["legacy_youtube_url"], "https://youtu.be/old")

    def test_queue_is_every_two_days_at_1830_vienna(self):
        now = datetime(2026, 8, 18, 10, 0, tzinfo=ZoneInfo("Europe/Vienna"))
        slots = make_shorts.plan_queue_slots(3, now=now)
        self.assertEqual([slot.strftime("%Y-%m-%d %H:%M %Z") for slot in slots], [
            "2026-08-18 18:30 CEST",
            "2026-08-20 18:30 CEST",
            "2026-08-22 18:30 CEST",
        ])


if __name__ == "__main__":
    unittest.main()
