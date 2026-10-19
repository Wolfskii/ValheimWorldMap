#!/usr/bin/env python3
"""Release-safety regressions; no network or publishing calls."""

import contextlib
import copy
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("nexus_release", Path(__file__).with_name("nexus-release.py"))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "nexus.yml"


def parse_outputs(text):
    """GITHUB_OUTPUT heredoc records (key<<delimiter / value / delimiter) as a dict."""
    return {key: value for key, _, value in
            re.findall(r"^(\w+)<<(value_\w+)\n(.*?)\n\2$", text, re.M | re.S)}


class ReleaseSafety(unittest.TestCase):
    def release(self):
        return {"tag_name": "v0.13.3", "draft": False, "prerelease": False, "published_at": "2026-09-09T16:16:31Z"}

    def test_only_published_latest_stable_release_is_accepted(self):
        self.assertEqual(sync.release_version(self.release()), "0.13.3")
        for key, value in [("draft", True), ("prerelease", True), ("published_at", None),
                           ("tag_name", "v0.13.3;echo unsafe"), ("tag_name", "v0.13.4-rc1")]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                sync.release_version({**self.release(), key: value})
        with self.assertRaisesRegex(ValueError, "downgrade"):
            sync.release_version(self.release(), "v0.13.2")

    def test_plugin_group_must_exist_once_and_old_full_group_is_ignored(self):
        plugin = {"id": "a", "name": "ValheimOne 0.13.2"}
        full = {"id": "b", "name": "ValheimOne Full 0.13.2"}
        self.assertEqual(sync.select_plugin_group([plugin]), "a")
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            self.assertEqual(sync.select_plugin_group([full, plugin]), "a")
        self.assertIn("Full: old file group ignored", log.getvalue())
        for invalid in [[], [full], [plugin, plugin], [plugin, full, plugin],
                        [{"id": "../another-mod", "name": "ValheimOne"}, full],
                        [{"id": "a", "name": "Another mod"}, full]]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError), \
                    contextlib.redirect_stdout(io.StringIO()):
                sync.select_plugin_group(invalid)

    def test_retry_does_not_duplicate_existing_or_blocked_versions(self):
        current = {"version": "0.13.3", "category": "main"}
        self.assertEqual(sync.existing_version([current], "0.13.3"), current)
        self.assertIsNone(sync.existing_version([current], "0.13.4"))
        for versions in [[current, current], [{**current, "category": "archived"}],
                         [{**current, "category": "removed"}], [{**current, "category": "unknown"}],
                         [{**current, "version": "0.14.0"}]]:
            with self.subTest(versions=versions), self.assertRaises(ValueError):
                sync.existing_version(versions, "0.13.3")

    def test_altered_artifacts_are_rejected_even_with_same_filenames(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            names = ["ValheimOne-0.13.3.zip", "ValheimOne-full-0.13.3.zip"]
            manifest = []
            for i, name in enumerate(names):
                payload = f"fixture archive {i}".encode()
                (folder / name).write_bytes(payload)
                manifest.append(f"{hashlib.sha256(payload).hexdigest()}  {name}")
            sums = folder / "SHA256SUMS-0.13.3.txt"
            sums.write_text("\n".join(manifest) + "\n")
            release = self.release()
            release["assets"] = [
                {"name": p.name, "size": len(p.read_bytes()), "digest": "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()}
                for p in folder.iterdir()
            ]
            self.assertEqual(sync.verify_assets(folder, release, "0.13.3"), names)
            (folder / names[0]).write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "digest/size"):
                sync.verify_assets(folder, release, "0.13.3")
            sums.write_text(manifest[0] + "\n" + manifest[0] + "\n")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                sync.verify_assets(folder, release, "0.13.3")

    def test_hash_match_must_belong_to_exact_mod_and_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "fixture.zip").write_bytes(b"fixture")
            current = {"game_scoped_id": "123"}
            good = {"mod": {"mod_id": 3571}, "file_details": {"file_id": 123}}
            with patch.object(sync, "nexus", return_value=[good]):
                sync.verify_indexed_hash(folder, "fixture.zip", current)
            for records in [[], [{**good, "mod": {"mod_id": 1}}],
                            [{**good, "file_details": {"file_id": 124}}]]:
                with patch.object(sync, "nexus", return_value=records), self.assertRaises(ValueError):
                    sync.verify_indexed_hash(folder, "fixture.zip", current)

    def test_api_redirects_never_forward_credentials(self):
        with self.assertRaisesRegex(RuntimeError, "not forwarded"):
            sync.NoRedirect().redirect_request(None, None, 302, "", {}, "https://unrelated.example")

    def download_status(self):
        # Plugin-only download plus the old Full file as Nexus shows it today (quarantined).
        return {"data": {
            "mod": {"name": "ValheimOne", "version": "0.13.10", "status": "published"},
            "modFiles": [
                {"fileId": 22082, "version": "0.13.10", "category": "MAIN", "primary": 1,
                 "manager": 0, "requirementsAlert": 1, "scannedV2": "VERIFIED"},
                {"fileId": 22083, "version": "0.13.10", "category": "MAIN", "primary": 0,
                 "manager": 1, "requirementsAlert": 0, "scannedV2": "QUARANTINED"},
            ],
        }}

    def verify_downloads(self, response):
        with contextlib.redirect_stdout(io.StringIO()):
            sync.verify_public_downloads(response, "0.13.10", {"game_scoped_id": "22082"})

    def test_plugin_only_download_passes(self):
        response = self.download_status()
        response["data"]["modFiles"].pop()
        self.verify_downloads(response)

    def test_old_full_file_is_ignored_in_any_scan_state(self):
        for status in ["QUARANTINED", "QUEUED", "NOT_SCANNED", "VERIFIED", "unknown", None]:
            for full_version, category in [("0.13.10", "MAIN"), ("0.13.9", "MAIN"), ("0.13.9", "OLD_VERSION")]:
                response = self.download_status()
                response["data"]["modFiles"][1].update(
                    scannedV2=status, version=full_version, category=category)
                with self.subTest(status=status, version=full_version, category=category):
                    self.verify_downloads(response)

    def test_plugin_quarantine_or_pending_scan_fails(self):
        for status in ["QUARANTINED", "QUEUED", "WAITING_REPORT", "NOT_SCANNED",
                       "REPORT_ERROR", "PARTIAL", "TOO_LARGE", "unknown", None]:
            response = self.download_status()
            response["data"]["modFiles"][0]["scannedV2"] = status
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.verify_downloads(response)

    def test_approved_scan_states_and_historical_quarantine_are_distinct(self):
        for status in ["VERIFIED", "INTERNALLY_VERIFIED", "MANUALLY_VERIFIED"]:
            response = self.download_status()
            response["data"]["modFiles"][0]["scannedV2"] = status
            historical = {**response["data"]["modFiles"][0], "fileId": 21189,
                          "version": "0.13.2", "category": "OLD_VERSION", "scannedV2": "QUARANTINED"}
            response["data"]["modFiles"].append(historical)
            self.verify_downloads(response)

    def test_page_version_and_each_download_control_must_agree(self):
        for field, value in [("version", "0.13.2"), ("status", "hidden"), ("name", "Another mod")]:
            response = self.download_status()
            response["data"]["mod"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify_downloads(response)
        for field, value in [("version", "0.13.2"), ("category", "OLD_VERSION"),
                             ("primary", 0), ("manager", 1), ("requirementsAlert", 0)]:
            response = self.download_status()
            response["data"]["modFiles"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify_downloads(response)

    def test_graphql_errors_missing_and_duplicate_file_records_fail_closed(self):
        response = self.download_status()
        missing = copy.deepcopy(response)
        missing["data"]["modFiles"].pop(0)
        duplicate = copy.deepcopy(response)
        duplicate["data"]["modFiles"].append(duplicate["data"]["modFiles"][0])
        for invalid in [{}, {"data": None}, {"errors": [{"message": "unavailable"}], **response},
                        missing, duplicate]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.verify_downloads(invalid)

    def todays_page(self):
        """Part of mod 3571 as Nexus listed it on 2026-10-19: the current plugin, the one before
        it, and two old Full archives that stayed quarantined."""
        def record(file_id, name, version, category, scan, primary=0, manager=0):
            return {"fileId": file_id, "name": name, "version": version, "category": category,
                    "primary": primary, "manager": manager, "requirementsAlert": 1, "scannedV2": scan}
        return {"data": {
            "mod": {"name": "ValheimOne", "version": "0.13.24", "status": "published"},
            "modFiles": [record(25316, "ValheimOne Full 0.13.20", "0.13.20", "OLD_VERSION", "QUARANTINED", manager=1),
                         record(25756, "ValheimOne 0.13.24", "0.13.24", "MAIN", "VERIFIED", primary=1),
                         record(25350, "ValheimOne Full 0.13.21", "0.13.21", "ARCHIVED", "QUARANTINED", manager=1),
                         record(25584, "ValheimOne 0.13.23", "0.13.23", "OLD_VERSION", "VERIFIED")],
        }}

    def test_report_lists_every_file_newest_first_and_names_the_newest_download(self):
        with contextlib.redirect_stdout(io.StringIO()) as log:
            table = sync.report_files(self.todays_page())
        self.assertEqual([line for line in table.splitlines() if re.match(r"\| \d", line)], [
            "| 25756 | ValheimOne 0.13.24 | 0.13.24 | MAIN | VERIFIED | yes |",
            "| 25584 | ValheimOne 0.13.23 | 0.13.23 | OLD_VERSION | VERIFIED | yes |",
            "| 25350 | ValheimOne Full 0.13.21 | 0.13.21 | ARCHIVED | QUARANTINED | no |",
            "| 25316 | ValheimOne Full 0.13.20 | 0.13.20 | OLD_VERSION | QUARANTINED | no |",
        ])
        self.assertIn("Newest downloadable plugin: 0.13.24 (file 25756, VERIFIED)", table)
        self.assertIn("Nexus file 25350: ValheimOne Full 0.13.21, 0.13.21, ARCHIVED, QUARANTINED, downloadable: no",
                      log.getvalue())
        self.assertIn("Nexus: Newest downloadable plugin: 0.13.24 (file 25756", log.getvalue())

    def test_only_a_listed_approved_plugin_counts_as_downloadable(self):
        page = self.todays_page()
        files = page["data"]["modFiles"]
        files[1]["scannedV2"] = "QUARANTINED"
        self.assertEqual(sync.newest_downloadable(sync.page_files(page)),
                         "Newest downloadable plugin: 0.13.23 (file 25584, VERIFIED)")
        files[3]["category"] = "ARCHIVED"
        files.append({"fileId": 25800, "name": "ValheimOne Full 0.13.25", "version": "0.13.25",
                      "category": "MAIN", "scannedV2": "VERIFIED"})
        for status in ["QUEUED", "NOT_SCANNED", "unknown", None]:
            files.append({"fileId": 25900, "name": "ValheimOne 0.13.26", "version": "0.13.26",
                          "category": "OLD_VERSION", "scannedV2": status})
            with self.subTest(status=status):
                self.assertEqual(sync.newest_downloadable(sync.page_files(page)), "Newest downloadable "
                                 "plugin: none (no plugin archive on the Files tab has passed its scan)")
            files.pop()

    def test_report_tolerates_a_failed_or_empty_query(self):
        for response in [{}, {"data": None}, {"errors": [{"message": "unavailable"}]},
                         {"data": {"modFiles": None}}, {"data": {"modFiles": ["not a record"]}}]:
            with self.subTest(response=response), contextlib.redirect_stdout(io.StringIO()):
                table = sync.report_files(response)
            self.assertIn("| - | no file records returned | - | - | - | - |", table)
            self.assertIn("Newest downloadable plugin: none", table)

    def test_nexus_fields_cannot_break_the_log_or_the_table(self):
        page = {"data": {"modFiles": [{"fileId": "1|2", "name": "x\n::error::boom | y", "version": "0.13.1`",
                                       "category": "MAIN", "scannedV2": "VERIFIED\n::notice::z"}]}}
        with contextlib.redirect_stdout(io.StringIO()) as log:
            table = sync.report_files(page)
        self.assertNotIn("::", log.getvalue() + table)
        self.assertTrue(all(line.startswith("Nexus") for line in log.getvalue().splitlines()))
        row = [line for line in table.splitlines() if line.startswith("| 1")]
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0].count("|"), 7)

    def test_failures_name_the_newest_download_and_a_cleared_plugin_adds_a_notice(self):
        newest = "Newest downloadable plugin: 0.13.23 (file 25584, VERIFIED)"
        for status, message in [("QUARANTINED", "quarantined"), ("QUEUED", "not passed its scan")]:
            page = self.todays_page()
            page["data"]["modFiles"][1]["scannedV2"] = status
            with self.subTest(status=status), \
                    self.assertRaisesRegex(ValueError, re.escape(message) + ".*" + re.escape(newest)):
                sync.verify_public_downloads(page, "0.13.24", {"game_scoped_id": "25756"})
        for status in sync.APPROVED_SCANS:
            page = self.todays_page()
            page["data"]["modFiles"][1]["scannedV2"] = status
            with self.subTest(status=status), contextlib.redirect_stdout(io.StringIO()) as log:
                sync.verify_public_downloads(page, "0.13.24", {"game_scoped_id": "25756"})
            self.assertIn(f"::notice::Nexus plugin 0.13.24 scan cleared ({status})", log.getvalue())


class PluginOnlyMirror(unittest.TestCase):
    """End-to-end prepare/verify runs against a fake Nexus API that refuses any Full lookup."""

    VERSION = "0.13.3"
    PLUGIN_GROUP, FULL_GROUP, PLUGIN_FILE = "7831647", "7831650", "25349"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        names = [f"ValheimOne-{self.VERSION}.zip", f"ValheimOne-full-{self.VERSION}.zip"]
        manifest = []
        for i, name in enumerate(names):
            payload = f"fixture archive {i}".encode()
            (self.folder / name).write_bytes(payload)
            manifest.append(f"{hashlib.sha256(payload).hexdigest()}  {name}")
        (self.folder / f"SHA256SUMS-{self.VERSION}.txt").write_text("\n".join(manifest) + "\n")
        self.plugin_md5 = hashlib.md5((self.folder / names[0]).read_bytes()).hexdigest()
        self.release = {
            "tag_name": "v" + self.VERSION, "draft": False, "prerelease": False,
            "published_at": "2026-10-14T00:00:00Z", "body": "notes",
            "assets": [{"name": p.name, "size": p.stat().st_size,
                        "digest": "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()}
                       for p in self.folder.iterdir()],
        }
        self.groups = [{"id": self.FULL_GROUP, "name": "ValheimOne Full 0.13.2"},
                       {"id": self.PLUGIN_GROUP, "name": "ValheimOne 0.13.2"}]
        self.plugin_versions = [{"version": self.VERSION, "category": "main", "is_primary": True,
                                 "game_scoped_id": self.PLUGIN_FILE}]
        self.plugin_scan = "VERIFIED"
        self.calls = []

    def fake_nexus(self, path, payload=None):
        self.calls.append(path)
        if path == "/v3/games/valheim/mods/3571":
            return {"data": {"id": "157", "game_scoped_id": "3571", "name": "ValheimOne", "game_id": "3667"}}
        if path == "/v3/mods/157/files":
            return {"data": {"mod_files": self.groups}}
        if path == f"/v3/mod-files/{self.PLUGIN_GROUP}/versions":
            return {"data": {"versions": self.plugin_versions}}
        if path == "/v2/graphql":
            files = [{"fileId": int(self.PLUGIN_FILE), "name": f"ValheimOne {self.VERSION}", "version": self.VERSION,
                      "category": "MAIN", "primary": 1, "manager": 0, "requirementsAlert": 1,
                      "scannedV2": self.plugin_scan}]
            if any(group["id"] == self.FULL_GROUP for group in self.groups):
                files.append({"fileId": 25350, "name": "ValheimOne Full 0.13.2", "version": "0.13.2",
                              "category": "MAIN", "primary": 0, "manager": 1, "requirementsAlert": 0,
                              "scannedV2": "QUARANTINED"})
            return {"data": {
                "mod": {"name": "ValheimOne", "version": self.VERSION, "status": "published"},
                "modFiles": files,
            }}
        if path == f"/v1/games/valheim/mods/md5_search/{self.plugin_md5}.json":
            return [{"mod": {"mod_id": 3571}, "file_details": {"file_id": int(self.PLUGIN_FILE)}}]
        if path == "/v1/games/valheim/mods/3571/changelogs.json":
            return {self.VERSION: ["notes"]}
        raise AssertionError(f"unexpected Nexus call (Full archive must not be read): {path}")

    def run_mode(self, mode):
        outputs = self.folder / "github-output"
        outputs.write_text("")
        env = {k: v for k, v in os.environ.items() if k not in ("RELEASE_TAG", "NEXUS_KNOWN_FULL_QUARANTINE")}
        env["GITHUB_OUTPUT"] = str(outputs)
        summary = self.folder / "step-summary"
        summary.write_text("")
        env["GITHUB_STEP_SUMMARY"] = str(summary)
        with patch.dict(os.environ, env, clear=True), \
                patch.object(sync, "github_release", return_value=self.release), \
                patch.object(sync, "nexus", side_effect=self.fake_nexus), \
                patch.object(sync.subprocess, "run") as download, \
                patch.object(sys, "argv", ["nexus-release.py", mode, "--directory", str(self.folder)]), \
                contextlib.redirect_stdout(io.StringIO()) as log:
            sync.main()
        self.assertEqual(download.call_count, 3 if mode == "prepare" else 0)
        text = outputs.read_text()
        keys = {line.split("<<")[0] for line in text.splitlines() if "<<value_" in line}
        return keys, text, log.getvalue()

    def test_verify_passes_plugin_only_with_old_full_group_present(self):
        keys, text, log = self.run_mode("verify")
        self.assertEqual(keys, {"version", "mod_id", "display_name", "description", "changelog",
                                "plugin_id", "plugin_upload"})
        self.assertIn(self.PLUGIN_GROUP, text)
        self.assertNotIn(self.FULL_GROUP, text)
        self.assertIn("Full: old file group ignored", log)
        self.assertNotIn(f"/v3/mod-files/{self.FULL_GROUP}/versions", self.calls)
        self.assertIn(f"/v1/games/valheim/mods/md5_search/{self.plugin_md5}.json", self.calls)

    def test_verify_passes_with_no_full_group_at_all(self):
        self.groups = self.groups[1:]
        keys, _, log = self.run_mode("verify")
        self.assertNotIn("Full", log)
        self.assertIn("plugin_upload", keys)

    def test_prepare_asks_for_plugin_upload_only(self):
        self.plugin_versions = []
        _, text, _ = self.run_mode("prepare")
        self.assertRegex(text, r"plugin_upload<<value_\w+\ntrue\n")
        self.assertNotIn("full_", text)

    def test_verify_fails_when_plugin_quarantined(self):
        self.plugin_scan = "QUARANTINED"
        with self.assertRaisesRegex(ValueError, "plugin archive is quarantined"):
            self.run_mode("verify")

    def test_verify_fails_when_plugin_group_missing(self):
        self.groups = self.groups[:1]
        with self.assertRaisesRegex(ValueError, "plugin-only file group"):
            self.run_mode("verify")

    def test_verify_fails_when_plugin_version_missing(self):
        self.plugin_versions = []
        with self.assertRaisesRegex(ValueError, "plugin version is missing"):
            self.run_mode("verify")

    def summary(self):
        return (self.folder / "step-summary").read_text()

    def test_verify_writes_every_file_state_once_including_the_old_full_file(self):
        _, _, log = self.run_mode("prepare")
        self.assertIn(f"Nexus file {self.PLUGIN_FILE}: ValheimOne {self.VERSION}, {self.VERSION}, MAIN, VERIFIED, "
                      "downloadable: yes", log)
        self.assertEqual(self.summary(), "")
        _, _, log = self.run_mode("verify")
        summary = self.summary()
        self.assertEqual(summary.count("### Nexus files on the page"), 1)
        self.assertEqual([line for line in summary.splitlines() if re.match(r"\| \d", line)], [
            "| 25350 | ValheimOne Full 0.13.2 | 0.13.2 | MAIN | QUARANTINED | no |",
            f"| {self.PLUGIN_FILE} | ValheimOne {self.VERSION} | {self.VERSION} | MAIN | VERIFIED | yes |",
        ])
        self.assertIn(f"Newest downloadable plugin: {self.VERSION} (file {self.PLUGIN_FILE}, VERIFIED)", summary)
        self.assertIn(f"::notice::Nexus plugin {self.VERSION} scan cleared (VERIFIED)", log)

    def test_failed_prepare_still_writes_the_file_states(self):
        self.plugin_scan = "QUARANTINED"
        with self.assertRaisesRegex(ValueError, "plugin archive is quarantined"):
            self.run_mode("prepare")
        self.assertIn(f"| {self.PLUGIN_FILE} | ValheimOne {self.VERSION} | {self.VERSION} | MAIN | QUARANTINED | no |",
                      self.summary())
        self.assertIn("Newest downloadable plugin: none", self.summary())

    def test_missing_version_still_reports_the_page_before_failing(self):
        self.plugin_versions = []
        with self.assertRaisesRegex(ValueError, "plugin version is missing"):
            self.run_mode("verify")
        self.assertIn("### Nexus files on the page", self.summary())

    def test_prepare_for_a_missing_version_reads_no_scan_state(self):
        self.plugin_versions = []
        self.run_mode("prepare")
        self.assertNotIn("/v2/graphql", self.calls)
        self.assertEqual(self.summary(), "")

    def test_github_integrity_still_covers_both_zips(self):
        (self.folder / f"ValheimOne-full-{self.VERSION}.zip").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "digest/size"):
            self.run_mode("verify")

    def test_description_is_one_short_line_whatever_the_release_notes(self):
        # Nexus's file form refuses a description over 255 characters (2026-10-14).
        url = f"https://github.com/humangenome/ValheimOne/releases/tag/v{self.VERSION}"
        for body in ["notes", "x" * 300, "## Fixed\n\n- " + "long line " * 8000]:
            self.plugin_versions = []
            self.release["body"] = body
            with self.subTest(body_length=len(body)):
                _, text, _ = self.run_mode("prepare")
                outputs = parse_outputs(text)
                self.assertEqual(outputs["description"],
                                 f"ValheimOne {self.VERSION}. Full release notes: {url}")
                self.assertLessEqual(len(outputs["description"]), 255)
                self.assertLessEqual(len(outputs["changelog"]), sync.CHANGELOG_MAX)
                if len(body) <= sync.CHANGELOG_MAX:
                    self.assertEqual(outputs["changelog"], body)
                else:
                    self.assertTrue(outputs["changelog"].endswith(f"Full release notes: {url}"))

    def test_empty_release_notes_fail_before_upload(self):
        self.plugin_versions = []
        self.release["body"] = "  \n"
        with self.assertRaisesRegex(ValueError, "Release notes are empty"):
            self.run_mode("prepare")

    def test_workflow_sends_only_the_bounded_outputs(self):
        text = WORKFLOW.read_text()
        step = text.split("uses: Nexus-Mods/upload-action@", 1)[1].split("\n      - name:", 1)[0]
        inputs = dict(re.findall(r"^          (\w+): (.*)$", step, re.M))
        for field in ["description", "changelog", "display_name", "version"]:
            self.assertEqual(inputs[field], "${{ steps.prepare.outputs.%s }}" % field)
        self.plugin_versions = []
        keys, _, _ = self.run_mode("prepare")
        used = set(re.findall(r"steps\.prepare\.outputs\.(\w+)", text))
        self.assertLessEqual(used, keys, "the workflow reads an output the script never writes")


class NexusFieldLimits(unittest.TestCase):
    """Every text field the upload sends stays inside Nexus's limits, for any accepted version."""

    LONGEST = "999999.999999.999999"

    def release(self, tag):
        return {"tag_name": tag, "draft": False, "prerelease": False, "published_at": "2026-10-14T00:00:00Z"}

    def test_longest_accepted_version_keeps_description_and_name_inside_the_limits(self):
        self.assertEqual(len(self.LONGEST), sync.VERSION_MAX)
        version = sync.release_version(self.release("v" + self.LONGEST))
        description = sync.nexus_description(version)
        self.assertLessEqual(len(description), 255)
        self.assertEqual(sync.DESCRIPTION_MAX, 255)
        # ASCII with nothing Nexus stores HTML-escaped, so the stored length is the sent length.
        self.assertTrue(description.isascii())
        self.assertFalse(set(description) & set("<>&\"'"))
        name = sync.nexus_display_name(version)
        self.assertLessEqual(len(name), 50)
        self.assertRegex(name, r"^[a-zA-Z0-9 _'().-]+$")

    def test_a_longer_version_is_refused_before_anything_is_uploaded(self):
        with self.assertRaisesRegex(ValueError, "longer than"):
            sync.release_version(self.release("v" + self.LONGEST + "9"))

    def test_description_guard_fails_instead_of_sending_a_long_line(self):
        with patch.object(sync, "REPOSITORY", "humangenome/" + "x" * 255), \
                self.assertRaisesRegex(ValueError, "over 255"):
            sync.nexus_description("0.13.22")


if __name__ == "__main__":
    unittest.main()
