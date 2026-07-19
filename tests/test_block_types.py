import unittest

from mining.block_types import (
    SUPER_UPPERCASE_MIN,
    classify_block,
    hash_digest_for_superblock,
    is_superblock,
    uppercase_count,
)


def _encoded_xen11(*, body_upper: int) -> str:
    """Build a full Argon2-looking hash with XEN11 and a target body uppercase count."""
    prefix = (
        "$argon2id$v=19$m=1100,t=1,p=1$"
        "c59/7GUZbuY1EHL+/F0xnvYvuDE$"  # salt (has some uppercase)
    )
    # Start with XEN11 + lowercase padding, then force body uppercase count.
    body = list("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaXEN11aaaaaa")
    # Normalize to target uppercase count in body only.
    upper_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    lower_chars = "abcdefghijklmnopqrstuvwxyz"
    # Ensure XEN11 is present (5 uppercase already if left alone — rebuild carefully)
    body = ["a"] * 80 + list("XEN11") + ["a"] * 1  # 86 chars, 5 upper from XEN11
    current = sum(1 for ch in body if ch.isupper())
    i = 0
    while current < body_upper:
        # flip lowercase to uppercase
        for j in range(len(body)):
            if body[j].islower() and body[j].isalpha():
                body[j] = upper_chars[i % 26]
                current += 1
                i += 1
                if current >= body_upper:
                    break
        else:
            body.append(upper_chars[i % 26])
            current += 1
            i += 1
    while current > body_upper:
        for j in range(len(body) - 1, -1, -1):
            if body[j].isupper() and "".join(body[j : j + 5]) != "XEN11":
                # don't break XEN11 if possible
                if j > 0 and "".join(body[max(0, j - 4) : j + 1]).find("XEN11") >= 0:
                    continue
                body[j] = "a"
                current -= 1
                break
        else:
            break
    # Guarantee XEN11 substring still present
    s = "".join(body)
    if "XEN11" not in s:
        s = s[:70] + "XEN11" + s[75:]
        s = s[:86] if len(s) >= 86 else s
    return prefix + s


class BlockTypesTests(unittest.TestCase):
    def test_threshold_matches_official_miner(self) -> None:
        self.assertEqual(SUPER_UPPERCASE_MIN, 50)

    def test_digest_extracts_body_from_encoded_hash(self) -> None:
        h = (
            "$argon2id$v=19$m=1100,t=1,p=1$c59/7GUZbuY1EHL+/F0xnvYvuDE$"
            "aaaaXEN11bbbbCCCCDDDDEEEEFFFF"
        )
        self.assertEqual(
            hash_digest_for_superblock(h),
            "aaaaXEN11bbbbCCCCDDDDEEEEFFFF",
        )

    def test_full_string_upper_is_not_used(self) -> None:
        """Salt uppercase must not inflate the superblock count."""
        # Body has only 5 upper (XEN11); full string has more due to salt.
        h = (
            "$argon2id$v=19$m=1100,t=1,p=1$ABCDEFGHIJK$aaaaaaaXEN11aaaaaaaa"
        )
        self.assertGreater(uppercase_count(h), uppercase_count(hash_digest_for_superblock(h)))
        self.assertFalse(is_superblock(h))
        self.assertEqual(classify_block(h), "XNM")

    def test_49_body_upper_is_normal(self) -> None:
        h = _encoded_xen11(body_upper=49)
        digest = hash_digest_for_superblock(h)
        self.assertIn("XEN11", digest)
        self.assertLess(uppercase_count(digest), 50)
        self.assertFalse(is_superblock(h))
        self.assertEqual(classify_block(h), "XNM")

    def test_50_body_upper_is_superblock(self) -> None:
        h = _encoded_xen11(body_upper=50)
        digest = hash_digest_for_superblock(h)
        self.assertGreaterEqual(uppercase_count(digest), 50)
        self.assertTrue(is_superblock(h))
        self.assertEqual(classify_block(h), "XBLK")

    def test_xuni_takes_priority_over_superblock(self) -> None:
        h = _encoded_xen11(body_upper=55).replace("XEN11", "XUNI5", 1)
        # may no longer have XEN11; force both markers
        if "XUNI" not in h:
            h = h[:-5] + "XUNI5"
        self.assertEqual(classify_block(h), "XUNI")

    def test_stale_xblk_hint_does_not_force_superblock(self) -> None:
        h = _encoded_xen11(body_upper=40)
        self.assertEqual(classify_block(h, "XBLK"), "XNM")

    def test_real_sample_near_superblock(self) -> None:
        # Shape of a real accepted hash (synthetic body with known upper count).
        h = _encoded_xen11(body_upper=52)
        self.assertEqual(classify_block(h), "XBLK")


if __name__ == "__main__":
    unittest.main()
