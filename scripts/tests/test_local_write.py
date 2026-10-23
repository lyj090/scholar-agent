#!/usr/bin/env python3
"""Unit tests for the Zotero 10 native write client and its safety semantics."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB_DIR))

from zotero.local_write import (  # noqa: E402
    ZoteroAuthRequired,
    ZoteroWriteClient,
    ZoteroWriteError,
    find_remembered_key,
    normalize_key,
)


class NormalizeKeyTest(unittest.TestCase):
    def test_accepts_and_uppercases_valid_key(self) -> None:
        self.assertEqual(normalize_key("abcd2345"), "ABCD2345")

    def test_rejects_wrong_length(self) -> None:
        for bad in ("ABC", "ABCD23456", ""):
            with self.assertRaises(ZoteroWriteError):
                normalize_key(bad)

    def test_rejects_non_string(self) -> None:
        with self.assertRaises(ZoteroWriteError):
            normalize_key(None)  # type: ignore[arg-type]


class FindRememberedKeyTest(unittest.TestCase):
    def test_reads_only_remembered_keys(self) -> None:
        """A single-use key is consumed by its first write, so it must not be
        picked up as if it were reusable."""
        payload = {"keys": [
            {"key": "SINGLEUSEKEY", "remember": False},
            {"key": "REMEMBEREDKEY", "remember": True},
        ]}
        fake = Path("/tmp/fake-localAPIKeys.json")
        with patch("zotero.local_write.glob.glob", return_value=[str(fake)]):
            with patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(payload))):
                self.assertEqual(find_remembered_key(), "REMEMBEREDKEY")

    def test_returns_none_when_absent(self) -> None:
        with patch("zotero.local_write.glob.glob", return_value=[]):
            self.assertIsNone(find_remembered_key())


class WriteClientTest(unittest.TestCase):
    def _client(self) -> ZoteroWriteClient:
        return ZoteroWriteClient("http://127.0.0.1:23119", "0", api_key="K" * 32)

    def test_rejects_bad_url_and_missing_user(self) -> None:
        with self.assertRaises(ZoteroWriteError):
            ZoteroWriteClient("ftp://x", "0", api_key="k")
        with self.assertRaises(ZoteroWriteError):
            ZoteroWriteClient("http://x", "", api_key="k")

    def test_server_id_is_cached_after_first_read(self) -> None:
        client = self._client()
        response = unittest.mock.MagicMock()
        response.headers = {"Zotero-Server-ID": "SID123"}
        response.read.return_value = b"{}"
        response.__enter__ = lambda s: s
        response.__exit__ = lambda *a: False
        with patch("zotero.local_write.urllib.request.urlopen", return_value=response) as call:
            self.assertEqual(client.server_id(), "SID123")
            self.assertEqual(client.server_id(), "SID123")
        self.assertEqual(call.call_count, 1, "server id should be cached")

    def test_missing_server_id_means_pre_z10(self) -> None:
        client = self._client()
        response = unittest.mock.MagicMock()
        response.headers = {}
        response.read.return_value = b"{}"
        response.__enter__ = lambda s: s
        response.__exit__ = lambda *a: False
        with patch("zotero.local_write.urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(ZoteroWriteError, "Zotero 10"):
                client.server_id()

    def test_auth_error_names_the_reason(self) -> None:
        client = self._client()
        with patch.object(ZoteroWriteClient, "server_id", return_value="SID"):
            with patch.object(
                ZoteroWriteClient, "_request", return_value=(401, "nope", {})
            ):
                with self.assertRaises(ZoteroAuthRequired):
                    client.get_item("ABCD2345")


class AddTagsSafetyTest(unittest.TestCase):
    """The API treats `tags` as a complete list. Writing only the new tag
    silently erases the rest, so add_tags must read-merge-write."""

    def test_merges_instead_of_overwriting(self) -> None:
        client = ZoteroWriteClient("http://127.0.0.1:23119", "0", api_key="K" * 32)
        existing = {"data": {"tags": [{"tag": "topic/existing"},
                                      {"tag": "status/to-read"}]},
                    "version": 5}
        sent: dict = {}

        def fake_request(method, path, **kwargs):
            sent.update(kwargs.get("body") or {})
            return 204, None, {}

        with patch.object(ZoteroWriteClient, "get_item", return_value=existing):
            with patch.object(ZoteroWriteClient, "_request", side_effect=fake_request):
                final = client.add_tags("ABCD2345", ["method/new"])

        self.assertEqual(
            sorted(t["tag"] for t in sent["tags"]),
            ["method/new", "status/to-read", "topic/existing"],
            "existing tags must survive",
        )
        self.assertIn("method/new", final)

    def test_is_idempotent(self) -> None:
        client = ZoteroWriteClient("http://127.0.0.1:23119", "0", api_key="K" * 32)
        existing = {"data": {"tags": [{"tag": "a"}]}, "version": 5}
        with patch.object(ZoteroWriteClient, "get_item", return_value=existing):
            with patch.object(ZoteroWriteClient, "_request") as call:
                final = client.add_tags("ABCD2345", ["a"])
        self.assertEqual(final, ["a"])
        call.assert_not_called()

    def test_reraises_after_exhausted_conflicts(self) -> None:
        client = ZoteroWriteClient("http://127.0.0.1:23119", "0", api_key="K" * 32)
        existing = {"data": {"tags": []}, "version": 1}
        with patch.object(ZoteroWriteClient, "get_item", return_value=existing):
            with patch.object(
                ZoteroWriteClient, "_request", return_value=(412, "stale", {})
            ):
                with self.assertRaises(Exception):
                    client.add_tags("ABCD2345", ["x"])

    def test_preserves_automatic_tag_type(self) -> None:
        """Regression: re-sending tags rebuilt from names alone dropped the
        ``type`` field, so Zotero's connector-added automatic tags were
        silently downgraded to manual on every write. The API represents an
        auto tag as ``{"tag": ..., "type": 1}`` and treats a missing ``type``
        as manual."""
        client = ZoteroWriteClient("http://127.0.0.1:23119", "0", api_key="K" * 32)
        existing = {
            "data": {
                "tags": [
                    {"tag": "Computer Science - Robotics", "type": 1},
                    {"tag": "status/to-read"},
                ]
            },
            "version": 5,
        }
        sent: dict = {}

        def fake_request(method, path, **kwargs):
            sent.update(kwargs.get("body") or {})
            return 204, None, {}

        with patch.object(ZoteroWriteClient, "get_item", return_value=existing):
            with patch.object(ZoteroWriteClient, "_request", side_effect=fake_request):
                client.add_tags("ABCD2345", ["method/new"])

        by_name = {t["tag"]: t for t in sent["tags"]}
        self.assertEqual(
            by_name["Computer Science - Robotics"].get("type"),
            1,
            "an automatic tag must stay automatic",
        )
        self.assertNotIn("type", by_name["method/new"],
                         "a newly added tag is manual by default")


class TriageTagTypeTest(unittest.TestCase):
    """triage is the bulk path; it must not reclassify tags either."""

    def test_triage_preserves_automatic_tag_type(self) -> None:
        client = ZoteroWriteClient("http://127.0.0.1:23119", "0", api_key="K" * 32)
        existing = {
            "data": {
                "collections": ["INBOX123"],
                "tags": [
                    {"tag": "Computer Science - Robotics", "type": 1},
                    {"tag": "/unread", "type": 1},
                ],
            },
            "version": 5,
        }
        sent: dict = {}

        def fake_request(method, path, **kwargs):
            sent.update(kwargs.get("body") or {})
            return 204, None, {}

        with patch.object(ZoteroWriteClient, "get_item", return_value=existing):
            with patch.object(ZoteroWriteClient, "_request", side_effect=fake_request):
                result = client.triage_item(
                    "ABCD2345",
                    target="COLL1234",
                    tags=["status/to-read"],
                    inbox="INBOX123",
                    drop_tags=("/unread",),
                )

        by_name = {t["tag"]: t for t in sent["tags"]}
        self.assertNotIn("/unread", by_name, "noise tag must be dropped")
        self.assertEqual(by_name["Computer Science - Robotics"].get("type"), 1)
        self.assertEqual(result["collections"], ["COLL1234"])


class PermanentDeleteGuardTest(unittest.TestCase):
    """`DELETE` erases the row and never touches the trash, so it must not be
    reachable by accident."""

    def test_refuses_without_explicit_confirmation(self) -> None:
        client = ZoteroWriteClient("http://127.0.0.1:23119", "0", api_key="K" * 32)
        with patch.object(ZoteroWriteClient, "_request") as call:
            with self.assertRaisesRegex(ZoteroWriteError, "without using the trash"):
                client.delete_permanently("ABCD2345")
        call.assert_not_called()

    def test_trash_uses_patch_not_delete(self) -> None:
        client = ZoteroWriteClient("http://127.0.0.1:23119", "0", api_key="K" * 32)
        seen: list[str] = []

        def record(method, path, **kwargs):
            seen.append(method)
            return 204, None, {}

        with patch.object(ZoteroWriteClient, "get_item",
                          return_value={"data": {}, "version": 1}):
            with patch.object(ZoteroWriteClient, "_request", side_effect=record):
                client.trash("ABCD2345")
        self.assertEqual(seen, ["PATCH"], "trash must be a reversible PATCH")


class PatchVersionTest(unittest.TestCase):
    def test_patch_reads_version_when_not_supplied(self) -> None:
        client = ZoteroWriteClient("http://127.0.0.1:23119", "0", api_key="K" * 32)
        with patch.object(ZoteroWriteClient, "item_version", return_value=9) as ver:
            with patch.object(
                ZoteroWriteClient, "_request", return_value=(204, None, {})
            ) as call:
                client.patch("ABCD2345", {"title": "x"})
        ver.assert_called_once()
        self.assertEqual(call.call_args.kwargs["version"], 9)

    def test_patch_requires_fields(self) -> None:
        client = ZoteroWriteClient("http://127.0.0.1:23119", "0", api_key="K" * 32)
        with self.assertRaises(ZoteroWriteError):
            client.patch("ABCD2345", {})

    def test_retries_on_conflict(self) -> None:
        client = ZoteroWriteClient("http://127.0.0.1:23119", "0", api_key="K" * 32)
        responses = [(412, "stale", {}), (204, None, {})]
        with patch.object(ZoteroWriteClient, "item_version", return_value=1):
            with patch.object(ZoteroWriteClient, "_request", side_effect=responses):
                client.patch("ABCD2345", {"title": "x"})  # must not raise


class UploadFlowTest(unittest.TestCase):
    """Phase 3 must send {"upload": key}; sending uploadKey is accepted but
    interpreted as a fresh authorization request, so the file never lands."""

    def test_three_phase_uses_upload_key_name(self) -> None:
        client = ZoteroWriteClient("http://127.0.0.1:23119", "0", api_key="K" * 32)
        calls: list[tuple] = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if len(calls) == 1:
                return 200, {"url": "http://local/upload/1", "uploadKey": "UK1"}, {}
            return 204, None, {}

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            handle.write(b"%PDF-1.4 test")
            path = handle.name

        with patch.object(ZoteroWriteClient, "_request", side_effect=fake_request):
            result = client.upload_file("ABCD2345", path)

        self.assertTrue(result["uploaded"])
        self.assertEqual(len(calls), 3, "upload is a three-phase flow")
        third_body = calls[2][2].get("body")
        self.assertEqual(third_body, {"upload": "UK1"})
        self.assertNotIn("uploadKey", third_body)

    def test_existing_file_short_circuits(self) -> None:
        client = ZoteroWriteClient("http://127.0.0.1:23119", "0", api_key="K" * 32)
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            handle.write(b"same")
            path = handle.name
        with patch.object(
            ZoteroWriteClient, "_request", return_value=(200, {"exists": 1}, {})
        ):
            result = client.upload_file("ABCD2345", path)
        self.assertFalse(result["uploaded"])
        self.assertEqual(result["reason"], "already present")


class MergeTest(unittest.TestCase):
    def test_merges_tags_and_reparents_children(self) -> None:
        client = ZoteroWriteClient("http://127.0.0.1:23119", "0", api_key="K" * 32)
        items = {
            "MASTER01": {"data": {"tags": [{"tag": "a"}], "collections": ["C1"]},
                         "version": 1},
            "DONOR001": {"data": {"tags": [{"tag": "b"}], "collections": ["C2"]},
                         "version": 1},
        }
        patched: list[tuple] = []
        trashed: list[str] = []

        with patch.object(ZoteroWriteClient, "get_item",
                          side_effect=lambda k: items[k]):
            with patch.object(ZoteroWriteClient, "get_children",
                              return_value=[{"key": "CHILD001"}]):
                with patch.object(ZoteroWriteClient, "patch",
                                  side_effect=lambda k, f, **kw: patched.append((k, f))):
                    with patch.object(ZoteroWriteClient, "trash",
                                      side_effect=trashed.append):
                        result = client.merge("MASTER01", ["DONOR001"])

        self.assertEqual(result["merged"], 1)
        self.assertEqual(result["moved"], 1)
        self.assertIn("CHILD001", [p[0] for p in patched], "child must be re-parented")
        master_fields = [p[1] for p in patched if p[0] == "MASTER01"][0]
        self.assertEqual(sorted(t["tag"] for t in master_fields["tags"]), ["a", "b"])
        self.assertEqual(sorted(master_fields["collections"]), ["C1", "C2"])
        self.assertEqual(trashed, ["DONOR001"], "donor must be trashed, not erased")

    def test_records_child_moves_for_reversal(self) -> None:
        """Without childMoves a merge could not be undone — this is what makes
        `write undo` possible where the plugin's merge was irreversible."""
        client = ZoteroWriteClient("http://127.0.0.1:23119", "0", api_key="K" * 32)
        items = {
            "MASTER01": {"data": {"tags": [], "collections": []}, "version": 1},
            "DONOR001": {"data": {"tags": [], "collections": []}, "version": 1},
        }
        with patch.object(ZoteroWriteClient, "get_item", side_effect=lambda k: items[k]):
            with patch.object(ZoteroWriteClient, "get_children",
                              return_value=[{"key": "CHILD001"}, {"key": "CHILD002"}]):
                with patch.object(ZoteroWriteClient, "patch"):
                    with patch.object(ZoteroWriteClient, "trash"):
                        result = client.merge("MASTER01", ["DONOR001"])

        self.assertEqual(
            result["childMoves"],
            [{"key": "CHILD001", "from": "DONOR001"},
             {"key": "CHILD002", "from": "DONOR001"}],
        )

    def test_rejects_master_also_listed_as_duplicate(self) -> None:
        client = ZoteroWriteClient("http://127.0.0.1:23119", "0", api_key="K" * 32)
        with self.assertRaises(ZoteroWriteError):
            client.merge("MASTER01", ["MASTER01"])


class UnmergeTest(unittest.TestCase):
    def test_returns_children_and_restores_donors(self) -> None:
        client = ZoteroWriteClient("http://127.0.0.1:23119", "0", api_key="K" * 32)
        patched: list[tuple] = []
        restored: list[str] = []
        with patch.object(ZoteroWriteClient, "patch",
                          side_effect=lambda k, f, **kw: patched.append((k, f))):
            with patch.object(ZoteroWriteClient, "restore", side_effect=restored.append):
                result = client.unmerge(
                    "MASTER01",
                    ["DONOR001"],
                    [{"key": "CHILD001", "from": "DONOR001"}],
                )
        self.assertEqual(patched, [("CHILD001", {"parentItem": "DONOR001"})])
        self.assertEqual(restored, ["DONOR001"])
        self.assertEqual(result["childrenReturned"], 1)
        self.assertEqual(result["restored"], ["DONOR001"])

    def test_partial_failure_does_not_abort_the_rest(self) -> None:
        client = ZoteroWriteClient("http://127.0.0.1:23119", "0", api_key="K" * 32)
        restored: list[str] = []

        def flaky_patch(key, fields, **kwargs):
            if key == "CHILD001":
                raise ZoteroWriteError("gone")

        with patch.object(ZoteroWriteClient, "patch", side_effect=flaky_patch):
            with patch.object(ZoteroWriteClient, "restore", side_effect=restored.append):
                result = client.unmerge(
                    "MASTER01",
                    ["DONOR001", "DONOR002"],
                    [{"key": "CHILD001", "from": "DONOR001"}],
                )
        self.assertEqual(result["childrenReturned"], 0)
        self.assertEqual(restored, ["DONOR001", "DONOR002"],
                         "one bad child must not block donor restoration")


class RenameAttachmentTest(unittest.TestCase):
    """Renaming must move the file too, not just relabel the database row.

    Measured against Zotero 10.0.5: after ``PATCH {"filename": ...}`` the item
    reports the new name while ``storage/<key>/`` still holds the old file. The
    two then disagree, and the next local read resolves the recorded name to
    nothing — MinerU fails with "PDF file not found (linked attachment?)".
    """

    def _client(self, data_dir: str) -> ZoteroWriteClient:
        return ZoteroWriteClient(
            "http://127.0.0.1:23119", "0", api_key="K" * 32, data_dir=data_dir
        )

    def _make_attachment(self, data_dir: str, key: str, filename: str) -> str:
        folder = os.path.join(data_dir, "storage", key)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, filename)
        with open(path, "wb") as handle:
            handle.write(b"%PDF-1.4 body")
        return path

    def test_file_is_moved_along_with_the_row(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as data_dir:
            old = self._make_attachment(data_dir, "ATT00001", "old-name.pdf")
            client = self._client(data_dir)
            envelope = {
                "data": {
                    "itemType": "attachment",
                    "linkMode": "imported_file",
                    "filename": "old-name.pdf",
                    "path": "storage:old-name.pdf",
                },
                "version": 7,
            }
            with patch.object(ZoteroWriteClient, "get_item", return_value=envelope):
                with patch.object(ZoteroWriteClient, "patch") as patch_call:
                    result = client.rename_attachment("ATT00001", "new-name.pdf")

            self.assertFalse(os.path.exists(old), "old file must be gone")
            self.assertTrue(
                os.path.isfile(os.path.join(data_dir, "storage", "ATT00001", "new-name.pdf"))
            )
            patch_call.assert_called_once_with("ATT00001", {"filename": "new-name.pdf"})
            self.assertTrue(result["fileMoved"])

    def test_diverged_pair_is_repaired(self) -> None:
        """A metadata-only rename leaves the row naming a file that does not
        exist; the next rename must find the real bytes and fix both sides."""
        import tempfile

        with tempfile.TemporaryDirectory() as data_dir:
            self._make_attachment(data_dir, "ATT00001", "actual-on-disk.pdf")
            client = self._client(data_dir)
            envelope = {
                "data": {
                    "itemType": "attachment",
                    "linkMode": "imported_file",
                    "filename": "stale-in-db.pdf",
                    "path": "storage:stale-in-db.pdf",
                },
                "version": 7,
            }
            with patch.object(ZoteroWriteClient, "get_item", return_value=envelope):
                with patch.object(ZoteroWriteClient, "patch"):
                    result = client.rename_attachment("ATT00001", "fixed.pdf")

            folder = os.path.join(data_dir, "storage", "ATT00001")
            self.assertEqual(os.listdir(folder), ["fixed.pdf"])
            self.assertTrue(result["fileMoved"])

    def test_refuses_to_overwrite_an_existing_file(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as data_dir:
            self._make_attachment(data_dir, "ATT00001", "a.pdf")
            self._make_attachment(data_dir, "ATT00001", "b.pdf")
            client = self._client(data_dir)
            envelope = {
                "data": {
                    "itemType": "attachment",
                    "linkMode": "imported_file",
                    "filename": "a.pdf",
                    "path": "storage:a.pdf",
                },
                "version": 7,
            }
            with patch.object(ZoteroWriteClient, "get_item", return_value=envelope):
                with self.assertRaises(ZoteroWriteError) as ctx:
                    client.rename_attachment("ATT00001", "b.pdf")
            self.assertIn("already exists", str(ctx.exception))

    def test_patch_failure_restores_the_file(self) -> None:
        """The disk move is the reversible half; a failed write must not leave
        the row and the disk describing different names."""
        import tempfile

        with tempfile.TemporaryDirectory() as data_dir:
            old = self._make_attachment(data_dir, "ATT00001", "old.pdf")
            client = self._client(data_dir)
            envelope = {
                "data": {
                    "itemType": "attachment",
                    "linkMode": "imported_file",
                    "filename": "old.pdf",
                    "path": "storage:old.pdf",
                },
                "version": 7,
            }
            with patch.object(ZoteroWriteClient, "get_item", return_value=envelope):
                with patch.object(
                    ZoteroWriteClient, "patch",
                    side_effect=ZoteroWriteError("nope"),
                ):
                    with self.assertRaises(ZoteroWriteError):
                        client.rename_attachment("ATT00001", "new.pdf")
            self.assertTrue(os.path.isfile(old), "file must be rolled back")

    def test_path_like_name_is_refused(self) -> None:
        client = self._client("/tmp")
        with self.assertRaises(ZoteroWriteError) as ctx:
            client.rename_attachment("ATT00001", "../escape.pdf")
        self.assertIn("bare filename", str(ctx.exception))

    def test_metadata_only_when_no_file_is_found(self) -> None:
        """A linked attachment outside storage/ still gets its row corrected."""
        import tempfile

        with tempfile.TemporaryDirectory() as data_dir:
            client = self._client(data_dir)
            envelope = {
                "data": {
                    "itemType": "attachment",
                    "linkMode": "linked_file",
                    "filename": "old.pdf",
                    "path": "/nonexistent/nope.pdf",
                },
                "version": 7,
            }
            with patch.object(ZoteroWriteClient, "get_item", return_value=envelope):
                with patch.object(ZoteroWriteClient, "patch") as patch_call:
                    result = client.rename_attachment("ATT00001", "new.pdf")
            patch_call.assert_called_once()
            self.assertFalse(result["fileMoved"])


class SnapshotRestoreTest(unittest.TestCase):
    """Undo must move the file back, not just relabel the row.

    ``write undo`` after a rename recorded itself as fully revertible while
    capturing only ``title`` — a field the rename never writes. Once rename began
    moving files, replaying that snapshot could not have restored the attachment
    even in principle.
    """

    def _client(self) -> ZoteroWriteClient:
        return ZoteroWriteClient(
            "http://127.0.0.1:23119", "0", api_key="K" * 32, data_dir="/tmp"
        )

    def test_snapshot_includes_attachment_fields(self) -> None:
        client = self._client()
        envelope = {"data": {"filename": "a.pdf", "path": "storage:a.pdf"}, "version": 1}
        with patch.object(ZoteroWriteClient, "get_item", return_value=envelope):
            snap = client.snapshot("ATT00001")
        self.assertEqual(snap["filename"], "a.pdf")
        self.assertEqual(snap["path"], "storage:a.pdf")

    def test_snapshot_omits_unset_fields(self) -> None:
        """Absent fields must not be written back as null."""
        client = self._client()
        envelope = {"data": {"title": "T"}, "version": 1}
        with patch.object(ZoteroWriteClient, "get_item", return_value=envelope):
            snap = client.snapshot("ATT00001")
        self.assertNotIn("filename", snap)

    def test_filename_restore_goes_through_rename(self) -> None:
        """A raw PATCH would relabel the row without moving the file."""
        client = self._client()
        calls: list[tuple] = []
        envelope = {"data": {"filename": "new.pdf"}, "version": 1}
        with patch.object(ZoteroWriteClient, "get_item", return_value=envelope):
            with patch.object(
                ZoteroWriteClient, "rename_attachment",
                side_effect=lambda k, n: calls.append((k, n)),
            ):
                client.restore_snapshot("ATT00001", {"filename": "old.pdf"})
        self.assertEqual(calls, [("ATT00001", "old.pdf")])

    def test_matching_filename_is_not_renamed_again(self) -> None:
        client = self._client()
        calls: list[tuple] = []
        envelope = {"data": {"filename": "old.pdf"}, "version": 1}
        with patch.object(ZoteroWriteClient, "get_item", return_value=envelope):
            with patch.object(
                ZoteroWriteClient, "rename_attachment",
                side_effect=lambda k, n: calls.append((k, n)),
            ):
                client.restore_snapshot("ATT00001", {"filename": "old.pdf"})
        self.assertEqual(calls, [])

    def test_tags_restore_is_a_plain_patch(self) -> None:
        client = self._client()
        patched: dict = {}
        with patch.object(
            ZoteroWriteClient, "patch",
            side_effect=lambda k, f, **kw: patched.update(f),
        ):
            client.restore_snapshot("ATT00001", {"tags": [{"tag": "old"}]})
        self.assertEqual(patched, {"tags": [{"tag": "old"}]})


class WriteClientConstructionTest(unittest.TestCase):
    def test_client_is_returned_when_already_writable(self) -> None:
        from zotero.local_write import build_write_client

        config = MagicMock()
        config.api_url = "http://127.0.0.1:23119"
        config.user_id = "0"
        config.data_dir = "/tmp"
        with patch.object(ZoteroWriteClient, "check_writable", return_value=True):
            with patch("zotero.launcher.ZoteroLauncher") as launcher:
                build_write_client(config)
        launcher.assert_not_called()

    def test_zotero_is_started_when_not_writable(self) -> None:
        """The defect this guards: writes used to fail outright when Zotero was
        closed, so the user had to start it by hand and retry."""
        from zotero.local_write import build_write_client

        config = MagicMock()
        config.api_url = "http://127.0.0.1:23119"
        config.user_id = "0"
        config.data_dir = "/tmp"
        with patch.object(ZoteroWriteClient, "check_writable", return_value=False):
            with patch("zotero.launcher.ZoteroLauncher") as launcher:
                launcher.return_value.ensure_zotero_running.return_value = {
                    "status": "started", "message": "Zotero started"
                }
                build_write_client(config)
        launcher.return_value.ensure_zotero_running.assert_called_once()

    def test_unstartable_zotero_raises_a_clear_error(self) -> None:
        from zotero.local_write import build_write_client

        config = MagicMock()
        config.api_url = "http://127.0.0.1:23119"
        config.user_id = "0"
        config.data_dir = "/tmp"
        with patch.object(ZoteroWriteClient, "check_writable", return_value=False):
            with patch("zotero.launcher.ZoteroLauncher") as launcher:
                launcher.return_value.ensure_zotero_running.return_value = {
                    "status": "error",
                    "message": "Zotero is not running and could not be started",
                    "diagnostic": "No Zotero executable found",
                }
                with self.assertRaises(ZoteroWriteError) as ctx:
                    build_write_client(config)
        self.assertIn("could not be started", str(ctx.exception))
        self.assertIn("No Zotero executable", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
