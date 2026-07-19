import unittest

from networking.difficulty import accept_network_difficulty


class DifficultyTests(unittest.TestCase):
    def test_accepts_lower_live_difficulty(self) -> None:
        self.assertEqual(accept_network_difficulty(100, fallback=1100), 100)

    def test_accepts_higher_live_difficulty(self) -> None:
        self.assertEqual(accept_network_difficulty(1200, fallback=1100), 1200)

    def test_invalid_value_uses_fallback(self) -> None:
        self.assertEqual(accept_network_difficulty(0, fallback=1100), 1100)
        self.assertEqual(accept_network_difficulty(-5, fallback=100), 100)


if __name__ == "__main__":
    unittest.main()