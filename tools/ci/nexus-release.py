#!/usr/bin/env python3
"""Prepare and verify the Nexus mirror of a published GitHub release. No API writes.

Nexus carries the plugin-only archive. The Full archive (plugin plus BepInEx) is published
on GitHub releases only: Nexus quarantined it on every release from 0.13.10 to 0.13.21.
Both GitHub zips and the checksum manifest are still verified before anything is mirrored.
An old Full file group left on the Nexus page is ignored: it is not required, not read
and never uploaded to."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.request
import uuid


REPOSITORY = "humangenome/ValheimOne"
MOD_PAGE_ID = "3571"
PLUGIN_GROUP = r"ValheimOne(?: \d+\.\d+\.\d+)?"
FULL_GROUP = r"ValheimOne Full(?: \d+\.\d+\.\d+)?"
APPROVED_SCANS = ("VERIFIED", "INTERNALLY_VERIFIED", "MANUALLY_VERIFIED")
# File categories listed on the public Files tab. ARCHIVED files sit in the file archive and
# removed ones are gone, so a file only counts as downloadable in one of these.
LISTED_CATEGORIES = ("MAIN", "UPDATE", "OPTIONAL", "OLD_VERSION", "MISCELLANEOUS")
# Nexus field limits. Version, name and changelog: the API's own schema
# (https://api.nexusmods.com/openapi.yaml, CreateModFileVersionRequest and
# AddModChangelogEntriesRequest, read 2026-10-14). Description: the Nexus file edit form
# rejects more than 255 characters (seen 2026-10-14). The API took the whole 738-character
# release body on 2026-10-13, but a file stored with more than 255 cannot be edited in the
# form afterwards, and the API may adopt the same limit. So the description is one short
# fixed line and the full notes go in the changelog.
VERSION_MAX = 20  # well inside Nexus's 50; keeps the name and description bounded
NAME_MAX = 50
NAME_PATTERN = r"[a-zA-Z0-9 _'().-]+"
DESCRIPTION_MAX = 255
CHANGELOG_MAX = 65535


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Unexpected API redirect; credentials were not forwarded")


def nexus(path, payload=None):
    key = os.environ.get("NEXUSMODS_API_KEY", "")
    if not key:
        raise RuntimeError("NEXUSMODS_API_KEY is missing; configure the repository secret")
    request = urllib.request.Request(
        "https://api.nexusmods.com" + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"apikey": key, "Accept": "application/json", "Content-Type": "application/json",
                 "User-Agent": "ValheimOne-release-sync"},
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=45) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Nexus API returned HTTP {error.code} for {path}") from None


def github_release():
    result = subprocess.run(
        ["gh", "api", f"repos/{REPOSITORY}/releases/latest"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def release_version(release, requested=""):
    tag = release.get("tag_name", "")
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("Expected a stable vMAJOR.MINOR.PATCH release")
    if release.get("draft", True) or release.get("prerelease", True) or not release.get("published_at"):
        raise ValueError("Only a published stable release can be mirrored")
    if len(tag) - 1 > VERSION_MAX:
        raise ValueError("Release version is longer than the Nexus mirror accepts")
    if requested and requested != tag:
        raise ValueError("Requested release is no longer Latest; refusing to downgrade Nexus")
    return tag[1:]


def release_url(version):
    return f"https://github.com/{REPOSITORY}/releases/tag/v{version}"


def nexus_display_name(version):
    name = f"ValheimOne {version}"
    if len(name) > NAME_MAX or not re.fullmatch(NAME_PATTERN, name):
        raise ValueError("Nexus file name is too long or has characters Nexus refuses")
    return name


def nexus_description(version):
    """One short fixed line; never the release body (see DESCRIPTION_MAX)."""
    text = f"ValheimOne {version}. Full release notes: {release_url(version)}"
    if len(text) > DESCRIPTION_MAX:
        raise ValueError(f"Nexus file description is over {DESCRIPTION_MAX} characters")
    return text


def nexus_changelog(body, version):
    """The full release notes, cut with a link back only if they pass Nexus's limit."""
    text = (body or "").strip()
    if not text:
        raise ValueError("Release notes are empty; Nexus needs a changelog entry")
    if len(text) <= CHANGELOG_MAX:
        return text
    tail = f"\n\nFull release notes: {release_url(version)}"
    return text[:CHANGELOG_MAX - len(tail)].rstrip() + tail


def verify_assets(folder, release, version):
    """Check both GitHub release zips and the checksum manifest (digest, size, manifest)."""
    names = [f"ValheimOne-{version}.zip", f"ValheimOne-full-{version}.zip"]
    sums_name = f"SHA256SUMS-{version}.txt"
    assets = {a["name"]: a for a in release["assets"]}
    expected = {}
    for line in (folder / sums_name).read_text().splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})\s+\*?([^/\\]+)", line)
        if not match or match[2] in expected:
            raise ValueError("Malformed or duplicate release checksum entry")
        expected[match[2]] = match[1]
    if set(expected) != set(names):
        raise ValueError("Release checksum manifest must name exactly the two release zips")
    for name in [sums_name, *names]:
        data = (folder / name).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        asset = assets[name]
        if asset.get("digest") != "sha256:" + digest or asset["size"] != len(data):
            raise ValueError(f"GitHub asset digest/size mismatch: {name}")
        if name in expected and expected[name] != digest:
            raise ValueError(f"Release checksum mismatch: {name}")
    return names


def select_plugin_group(files):
    """Return the ID of the one existing plugin-only file group.

    An old Full file group is tolerated and left alone: it is not required and the
    mirror never reads its versions or uploads to it."""
    plugin = []
    for file in files:
        name = file.get("name", "")
        if re.fullmatch(PLUGIN_GROUP, name, re.IGNORECASE):
            plugin.append(str(file["id"]))
        elif re.fullmatch(FULL_GROUP, name, re.IGNORECASE):
            print("Nexus Full: old file group ignored; the Full archive is on GitHub releases only")
    if not plugin:
        raise ValueError("Expected the existing plugin-only file group on Nexus")
    if len(plugin) > 1:
        raise ValueError("Multiple Nexus plugin file groups; review their API IDs")
    if not re.fullmatch(r"[A-Za-z0-9-]+", plugin[0]):
        raise ValueError("Unexpected Nexus file ID")
    return plugin[0]


def existing_version(versions, version):
    target = tuple(map(int, version.split(".")))
    for item in versions:
        live_version = item.get("version", "")
        if item.get("category") == "main" and re.fullmatch(r"\d+\.\d+\.\d+", live_version):
            if tuple(map(int, live_version.split("."))) > target:
                raise ValueError("Nexus already has a newer active version; refusing to downgrade")
    matches = [v for v in versions if v.get("version") == version]
    if len(matches) > 1:
        raise ValueError("Duplicate versions on Nexus; review instead of uploading another copy")
    if matches and matches[0].get("category") != "main":
        raise ValueError("Matching Nexus version is not an active main file; manual review required")
    return matches[0] if matches else None


def verify_indexed_hash(folder, filename, current):
    # Nexus's legacy hash index identifies the actual stored archive. SHA-256 above
    # remains the release integrity check; this MD5 lookup is only index reconciliation.
    digest = hashlib.md5((folder / filename).read_bytes(), usedforsecurity=False).hexdigest()
    matches = nexus(f"/v1/games/valheim/mods/md5_search/{digest}.json")
    if not any(
        str(m.get("mod", {}).get("mod_id")) == MOD_PAGE_ID
        and str(m.get("file_details", {}).get("file_id")) == str(current["game_scoped_id"])
        for m in matches
    ):
        raise ValueError("Nexus has not indexed the expected archive; do not upload a duplicate")


def public_download_status(game_id):
    # The legacy v1 mod metadata can lag behind the live page after a v3 upload.
    # GraphQL is a read-only query for the page version and per-file scan status.
    return nexus("/v2/graphql", {
        "query": """query($mod:ID!,$game:ID!) {
            mod(modId:$mod,gameId:$game) { name version status }
            modFiles(modId:$mod,gameId:$game) {
                fileId name version category primary manager requirementsAlert scannedV2
            }
        }""",
        "variables": {"mod": MOD_PAGE_ID, "game": str(game_id)},
    })


def page_files(response):
    """Every file record on the page, newest upload first (Nexus numbers files in upload order)."""
    data = (response or {}).get("data") or {}
    files = [f for f in (data.get("modFiles") or []) if isinstance(f, dict)]
    return sorted(files, key=file_number, reverse=True)


def file_number(file):
    try:
        return int(file.get("fileId"))
    except (TypeError, ValueError):
        return -1


def is_downloadable(file):
    return file.get("scannedV2") in APPROVED_SCANS and file.get("category") in LISTED_CATEGORIES


def shown(value, limit=60):
    """A Nexus field as plain text that cannot break a log line, an annotation or a table cell."""
    text = re.sub(r"[^A-Za-z0-9 _'().-]", "?", "" if value is None else str(value))[:limit]
    return text or "-"


def newest_downloadable(files):
    """One line naming the newest plugin-only archive a visitor can download from the Files tab."""
    newest = next((f for f in files if re.fullmatch(PLUGIN_GROUP, str(f.get("name") or ""), re.IGNORECASE)
                   and is_downloadable(f)), None)
    if newest is None:
        return "Newest downloadable plugin: none (no plugin archive on the Files tab has passed its scan)"
    return (f"Newest downloadable plugin: {shown(newest.get('version'))} "
            f"(file {shown(newest.get('fileId'))}, {shown(newest.get('scannedV2'))})")


def report_files(response):
    """Log every file on the page with its scan state, newest upload first, and return the
    same as a step-summary table. A report only: verify_public_downloads decides pass or fail."""
    files = page_files(response)
    rows = []
    for file in files:
        number, name, version, category, scan = (
            shown(file.get(k)) for k in ("fileId", "name", "version", "category", "scannedV2"))
        downloadable = "yes" if is_downloadable(file) else "no"
        print(f"Nexus file {number}: {name}, {version}, {category}, {scan}, downloadable: {downloadable}")
        rows.append(f"| {number} | {name} | {version} | {category} | {scan} | {downloadable} |")
    newest = newest_downloadable(files)
    print(f"Nexus: {newest}")
    return "\n".join([
        "### Nexus files on the page", "",
        "| File | Name | Version | Place | Scan | Downloadable |", "|---|---|---|---|---|---|",
        *(rows or ["| - | no file records returned | - | - | - | - |"]), "", newest, ""])


def write_summary(text):
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if text and target:
        with open(target, "a") as stream:
            stream.write(text + "\n")


def verify_public_downloads(response, version, current):
    """Check the page version and the plugin-only download's controls and scan state.

    Only the plugin file is read. Every other file on the page, including an old Full
    archive in any scan state, is ignored. A quarantined, pending or unknown scan on the
    plugin fails the check."""
    if response.get("errors"):
        raise ValueError("Nexus page/scan query failed; download status is unverified")
    data = response.get("data") or {}
    mod = data.get("mod") or {}
    if mod.get("name") != "ValheimOne" or mod.get("status") != "published":
        raise ValueError("Nexus page is not the expected published mod")
    if mod.get("version") != version:
        raise ValueError("Nexus page version does not match GitHub Latest")
    matches = [f for f in (data.get("modFiles") or [])
               if str(f.get("fileId")) == str(current["game_scoped_id"])]
    if len(matches) != 1:
        raise ValueError("Nexus plugin download status is missing or ambiguous")
    file = matches[0]
    if file.get("version") != version or file.get("category") != "MAIN":
        raise ValueError("Nexus plugin download is not the expected active version")
    # Legacy manager=1 disables mod-manager downloads; 0 enables them.
    if file.get("primary") != 1 or file.get("manager") != 0 or file.get("requirementsAlert") != 1:
        raise ValueError("Nexus plugin primary/download/requirements controls do not match")
    status = file.get("scannedV2")
    newest = newest_downloadable(page_files(response))
    if status == "QUARANTINED":
        raise ValueError(f"Nexus plugin archive is quarantined; moderator review required. Do not reupload. {newest}")
    if status not in APPROVED_SCANS:
        raise ValueError(f"Nexus plugin archive has not passed its scan; download remains unverified. {newest}")
    print(f"::notice::Nexus plugin {version} scan cleared ({status}); it is the download on the Files tab")
    print("Nexus plugin: scan cleared and download controls verified")


def write_outputs(values):
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        return
    with open(target, "a") as stream:
        for key, value in values.items():
            delimiter = "value_" + uuid.uuid4().hex
            stream.write(f"{key}<<{delimiter}\n{value}\n{delimiter}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["assets", "prepare", "verify"])
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    release = github_release()
    version = release_version(release, os.environ.get("RELEASE_TAG", ""))
    folder = args.directory
    folder.mkdir(parents=True, exist_ok=True)
    if args.mode != "verify":
        for name in [f"ValheimOne-{version}.zip", f"ValheimOne-full-{version}.zip", f"SHA256SUMS-{version}.txt"]:
            subprocess.run([
                "gh", "release", "download", release["tag_name"], "--repo", REPOSITORY,
                "--dir", str(folder), "--pattern", name, "--clobber",
            ], check=True, stdout=subprocess.DEVNULL)
    plugin_archive, _full_archive = verify_assets(folder, release, version)
    if args.mode == "assets":
        print(f"GitHub {release['tag_name']}: both archives and checksum manifest verified")
        return
    mod = nexus(f"/v3/games/valheim/mods/{MOD_PAGE_ID}")["data"]
    if str(mod["game_scoped_id"]) != MOD_PAGE_ID or mod.get("name") != "ValheimOne":
        raise ValueError("Unexpected Nexus mod identity")
    mod_id = str(mod["id"])
    if not re.fullmatch(r"[A-Za-z0-9-]+", mod_id):
        raise ValueError("Unexpected Nexus mod ID")
    plugin_id = select_plugin_group(nexus(f"/v3/mods/{mod_id}/files")["data"]["mod_files"])
    versions = nexus(f"/v3/mod-files/{plugin_id}/versions")["data"]["versions"]
    current = existing_version(versions, version)
    outputs = {"version": version, "mod_id": mod_id,
               "display_name": nexus_display_name(version),
               "description": nexus_description(version),
               "changelog": nexus_changelog(release.get("body"), version),
               "plugin_id": plugin_id, "plugin_upload": "false" if current else "true"}
    # Every file's scan state goes to the step summary once per run: from the verify step, or
    # from whichever step fails first (a failed prepare skips verify).
    summary, failed = "", True
    try:
        page = None
        if current or args.mode == "verify":
            page = public_download_status(mod["game_id"])
            summary = report_files(page)
        if not current and args.mode == "verify":
            raise ValueError("Nexus plugin version is missing")
        print(f"Nexus plugin: {'version present' if current else 'upload required'}")
        if current:
            if not current.get("is_primary"):
                raise ValueError("Plugin-only must be the primary Nexus download")
            # Quarantined files can disappear from the legacy hash index. Report the
            # actual moderation state before that lookup can obscure it with a 404.
            verify_public_downloads(page, version, current)
            verify_indexed_hash(folder, plugin_archive, current)
            print("Nexus plugin: hash-index verified")
        if args.mode == "verify":
            changelogs = nexus(f"/v1/games/valheim/mods/{MOD_PAGE_ID}/changelogs.json")
            if version not in changelogs:
                raise ValueError("Nexus release changelog is missing")
            print("Nexus plugin version, archive index, primary download, page version and changelog verified")
            print("Scan state verified. Check the plugin download button on the Files page and download it normally.")
        failed = False
    finally:
        if failed or args.mode == "verify":
            write_summary(summary)
    write_outputs(outputs)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        # Do not include API response bodies, request headers, or credentials in CI logs.
        print(f"Nexus release check failed: {error}", file=sys.stderr)
        sys.exit(1)
