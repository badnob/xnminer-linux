import threading
import time
import unittest
from unittest.mock import patch

from core.models import NetworkStatus
from networking.poller import NetworkPoller


class NetworkPollerTests(unittest.TestCase):
    def test_poll_once_stores_status(self) -> None:
        poller = NetworkPoller("http://example.test/difficulty")
        ok = NetworkStatus(port80_up=True, difficulty=1100, latency_ms=12.0)
        with patch("networking.poller.fetch_difficulty", return_value=ok):
            status = poller.poll_once()
        self.assertEqual(status.difficulty, 1100)
        cached = poller.get_status()
        self.assertEqual(cached.difficulty, 1100)

    def test_background_poll_updates_cache(self) -> None:
        poller = NetworkPoller(
            "http://example.test/difficulty",
            poll_interval_s=0.2,
            down_poll_interval_s=0.2,
            timeout_s=1.0,
        )
        responses = [
            NetworkStatus(
                port80_up=False,
                difficulty=None,
                latency_ms=None,
                error="timed out",
            ),
            NetworkStatus(port80_up=True, difficulty=100, latency_ms=8.0),
        ]

        def fake_fetch(_url: str, timeout_s: int = 3) -> NetworkStatus:
            if responses:
                return responses.pop(0)
            return NetworkStatus(port80_up=True, difficulty=100, latency_ms=8.0)

        with patch("networking.poller.fetch_difficulty", side_effect=fake_fetch):
            poller.start(initial_timeout_s=1.0)
            deadline = time.time() + 2.0
            while time.time() < deadline:
                if poller.get_status().difficulty == 100:
                    break
                time.sleep(0.05)
            poller.stop()

        self.assertEqual(poller.get_status().difficulty, 100)


if __name__ == "__main__":
    unittest.main()