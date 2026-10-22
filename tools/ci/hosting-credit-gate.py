#!/usr/bin/env python3
"""Checks whether a ValheimWorldMap DLL carries the live map's sidebar footer link.

The live map pages are embedded in the DLL as gzip + base64 string literals
(src/ValheimWorldMap/LiveMap/Web/EmbeddedAssets.g.cs), so a plain `strings` or grep
over the binary cannot see the page text. This script finds every embedded
payload in the DLL's string heap, decompresses it, and searches the page text.

    --hosting-credit absent    the Thunderstore build: no payload may mention it
    --hosting-credit present   the GitHub build: index.html must carry it (this
                               is the positive control that proves the payloads
                               were found and decoded)

Either way the decoded payloads must include the map page itself, so an
extraction that finds nothing can never pass as "absent".

Usage: tools/ci/hosting-credit-gate.py <ValheimWorldMap.dll> --hosting-credit absent|present
"""

import argparse
import base64
import binascii
import gzip
import re
import sys
import zlib

# gzip magic 1f 8b 08 is "H4sI" in base64; the literals are UTF-16LE in the #US heap.
PAYLOAD = re.compile(rb"H\x004\x00s\x00I\x00(?:[A-Za-z0-9+/=]\x00){60,}")
CREDIT_MARKERS = (b"survivalservers", b"server hosting by")
INDEX_DOCTYPE = b"<!doctype html>"
INDEX_MARKER = b'class="sidebar-scroll"'
MINIMUM_PAYLOADS = 15


def decoded_payloads(dll_bytes):
    for match in PAYLOAD.finditer(dll_bytes):
        text = match.group(0).decode("utf-16-le")
        try:
            yield gzip.decompress(base64.b64decode(text, validate=True))
        except (binascii.Error, OSError, EOFError, zlib.error):
            continue


def is_index(payload):
    # index.html is the full page; embed.html (the panel fragment) has the same sidebar.
    return payload.lstrip().lower().startswith(INDEX_DOCTYPE) and INDEX_MARKER in payload


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dll")
    parser.add_argument("--hosting-credit", required=True, choices=("absent", "present"))
    args = parser.parse_args()

    with open(args.dll, "rb") as handle:
        dll_bytes = handle.read()

    payloads = list(decoded_payloads(dll_bytes))
    index_pages = [p for p in payloads if is_index(p)]
    credited = [p for p in payloads if any(m in p.lower() for m in CREDIT_MARKERS)]
    raw_hits = [
        m.decode() for m in CREDIT_MARKERS
        if m in dll_bytes.lower() or m.decode().encode("utf-16-le") in dll_bytes.lower()
    ]

    print("%s: %d embedded payloads decoded, %d map page(s), %d with the footer link"
          % (args.dll, len(payloads), len(index_pages), len(credited)))

    failures = []
    if len(payloads) < MINIMUM_PAYLOADS:
        failures.append("only %d embedded payloads decoded (expected at least %d)"
                        % (len(payloads), MINIMUM_PAYLOADS))
    if len(index_pages) != 1:
        failures.append("expected exactly one embedded map page, found %d" % len(index_pages))
    if raw_hits:
        failures.append("uncompressed footer text in the DLL: %s" % ", ".join(raw_hits))
    if args.hosting_credit == "absent" and credited:
        failures.append("%d embedded payload(s) carry the footer link" % len(credited))
    if args.hosting_credit == "present" and not any(is_index(p) for p in credited):
        failures.append("the map page does not carry the footer link")

    if failures:
        for failure in failures:
            print("HOSTING CREDIT GATE FAIL: " + failure, file=sys.stderr)
        return 1
    print("HOSTING CREDIT GATE PASS (%s)" % args.hosting_credit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
