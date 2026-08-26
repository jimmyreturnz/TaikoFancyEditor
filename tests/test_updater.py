"""Updater tests. Every HTTP call is stubbed -- nothing here touches the network."""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import updater


class _Response(io.BytesIO):
    """Minimal stand-in for what urlopen hands back."""

    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()
        return False


def opener_for(payload: bytes):
    def opener(request, timeout=None):
        del request, timeout
        return _Response(payload)
    return opener


def failing_opener(error: Exception):
    def opener(request, timeout=None):
        del request, timeout
        raise error
    return opener


RELEASE_PAYLOAD = {
    "tag_name": "v3.1.0",
    "body": "New things.",
    "html_url": "https://github.com/jimmyreturnz/TaikoFancyEditor/releases/tag/v3.1.0",
    "assets": [
        {
            "name": "TaikoFancyArranger-Windows-x64.zip",
            "browser_download_url": "https://example.invalid/TaikoFancyArranger-Windows-x64.zip",
        },
        {
            "name": "SHA256SUMS.txt",
            "browser_download_url": "https://example.invalid/SHA256SUMS.txt",
        },
    ],
}


class VersionTests(unittest.TestCase):
    def test_tag_prefix_is_stripped(self):
        self.assertEqual(updater.version_tuple("v3.1.0"), (3, 1, 0))
        self.assertEqual(updater.version_tuple(" 3.0.0\n"), (3, 0, 0))

    def test_comparison_is_numeric_not_lexicographic(self):
        self.assertTrue(updater.is_newer("3.10.0", "3.9.0"))
        self.assertFalse(updater.is_newer("3.9.0", "3.10.0"))
        self.assertTrue(updater.is_newer("v3.10.0", "3.9.9"))

    def test_no_update_when_equal(self):
        self.assertFalse(updater.is_newer("v3.0.0", "3.0.0"))

    def test_no_update_when_local_is_newer(self):
        self.assertFalse(updater.is_newer("v3.0.0", "3.1.0"))

    def test_shorter_versions_pad_with_zeros(self):
        self.assertFalse(updater.is_newer("3.1", "3.1.0"))
        self.assertTrue(updater.is_newer("3.1.1", "3.1"))

    def test_unparseable_versions_never_offer_an_update(self):
        for remote, local in (("", "3.0.0"), ("nightly", "3.0.0"), ("3.1.0", "")):
            with self.subTest(remote=remote, local=local):
                self.assertFalse(updater.is_newer(remote, local))

    def test_local_version_matches_the_repository_version_file(self):
        expected = (Path(updater.__file__).resolve().parent / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(updater.local_version(), expected)


class ReleaseLookupTests(unittest.TestCase):
    def test_release_is_parsed_with_both_assets(self):
        release = updater.parse_release(RELEASE_PAYLOAD)
        self.assertEqual(release.tag, "v3.1.0")
        self.assertEqual(release.version, "3.1.0")
        self.assertEqual(release.notes, "New things.")
        self.assertTrue(release.zip_url.endswith("TaikoFancyArranger-Windows-x64.zip"))
        self.assertTrue(release.checksums_url.endswith("SHA256SUMS.txt"))

    def test_missing_assets_leave_empty_urls_rather_than_failing(self):
        release = updater.parse_release({"tag_name": "v3.1.0"})
        self.assertEqual((release.zip_url, release.checksums_url), ("", ""))

    def test_payload_without_a_usable_tag_is_rejected(self):
        for payload in ({}, {"tag_name": ""}, {"tag_name": "nightly"}, [], None, "not json"):
            with self.subTest(payload=payload):
                self.assertIsNone(updater.parse_release(payload))

    def test_update_is_offered_when_the_release_is_newer(self):
        opener = opener_for(json.dumps(RELEASE_PAYLOAD).encode())
        release = updater.check_for_update("3.0.0", opener)
        self.assertIsNotNone(release)
        self.assertEqual(release.tag, "v3.1.0")

    def test_no_update_when_the_release_matches_the_running_version(self):
        opener = opener_for(json.dumps(RELEASE_PAYLOAD).encode())
        self.assertIsNone(updater.check_for_update("3.1.0", opener))

    def test_malformed_json_is_swallowed(self):
        self.assertIsNone(updater.fetch_latest_release(opener_for(b"<html>rate limited</html>")))

    def test_empty_response_is_swallowed(self):
        self.assertIsNone(updater.fetch_latest_release(opener_for(b"")))

    def test_json_that_is_not_an_object_is_swallowed(self):
        self.assertIsNone(updater.fetch_latest_release(opener_for(b"[1, 2, 3]")))

    def test_http_and_network_errors_are_swallowed(self):
        errors = (
            urllib.error.HTTPError("https://example.invalid", 403, "rate limited", {}, None),
            urllib.error.URLError("no network"),
            TimeoutError("timed out"),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.assertIsNone(updater.fetch_latest_release(failing_opener(error)))

    def test_non_https_urls_are_refused(self):
        with self.assertRaises(updater.UpdateError):
            updater._request("http://example.invalid/release.zip")

    def test_request_carries_a_user_agent(self):
        request = updater._request(updater.RELEASES_API)
        self.assertTrue(request.get_header("User-agent"))


class ChecksumTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.file = Path(self.directory.name) / "payload.zip"
        self.file.write_bytes(b"portable build")
        self.digest = hashlib.sha256(b"portable build").hexdigest()

    def sums(self, digest: str) -> str:
        return f"{'0' * 64}  TaikoFancyArranger.exe\n{digest}  {updater.PORTABLE_ASSET}\n"

    def test_expected_digest_is_read_from_the_sums_file(self):
        self.assertEqual(
            updater.expected_digest(self.sums(self.digest.upper()), updater.PORTABLE_ASSET),
            self.digest,
        )

    def test_expected_digest_is_none_when_the_file_is_not_listed(self):
        self.assertIsNone(updater.expected_digest(self.sums(self.digest), "other.zip"))
        self.assertIsNone(updater.expected_digest("", updater.PORTABLE_ASSET))
        self.assertIsNone(updater.expected_digest("garbage line\n", updater.PORTABLE_ASSET))

    def test_a_good_digest_is_accepted(self):
        self.assertTrue(updater.verify_digest(self.digest, self.digest.upper()))

    def test_a_bad_digest_is_rejected(self):
        self.assertFalse(updater.verify_digest(self.digest, "f" * 64))
        self.assertFalse(updater.verify_digest(self.digest, None))
        self.assertFalse(updater.verify_digest(self.digest, ""))

    def test_download_hashes_what_it_writes(self):
        destination = Path(self.directory.name) / "downloaded.zip"
        seen: list[int] = []
        actual = updater.download(
            "https://example.invalid/x.zip",
            destination,
            seen.append,
            opener_for(b"portable build"),
        )
        self.assertEqual(actual, self.digest)
        self.assertEqual(destination.read_bytes(), b"portable build")
        self.assertEqual(seen[-1], 100)

    def test_install_refuses_a_mismatched_archive_and_writes_nothing(self):
        release = updater.parse_release(RELEASE_PAYLOAD)
        parent = Path(self.directory.name) / "apps"
        parent.mkdir()
        updater_folder = updater.application_folder
        updater.application_folder = lambda: parent / "TaikoFancyArranger"
        self.addCleanup(setattr, updater, "application_folder", updater_folder)

        def opener(request, timeout=None):
            del timeout
            if request.full_url.endswith("SHA256SUMS.txt"):
                return _Response(self.sums("a" * 64).encode())
            return _Response(b"portable build")

        with self.assertRaises(updater.UpdateError):
            updater.install_beside(release, None, opener)
        self.assertEqual(list(parent.iterdir()), [])

    def test_install_without_assets_is_refused_before_any_request(self):
        release = updater.parse_release({"tag_name": "v3.1.0"})
        with self.assertRaises(updater.UpdateError):
            updater.install_beside(release, None, failing_opener(AssertionError("no request expected")))


class FolderTests(unittest.TestCase):
    def test_free_folder_never_reuses_an_existing_name(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            first = updater.free_folder(parent, "TaikoFancyArranger-3.1.0")
            first.mkdir()
            second = updater.free_folder(parent, "TaikoFancyArranger-3.1.0")
            self.assertNotEqual(first, second)
            self.assertFalse(second.exists())


if __name__ == "__main__":
    unittest.main()
