#!/usr/bin/env python3
"""Download the FTC's daily Do Not Call complaint files.

    python fetch_ftc_files.py --out ftcdata

The FTC links these from its data-sets page and keeps a rolling window of about
24 of them, so anything older has to have been captured earlier - which is why
build_spam_list.py keeps a running history rather than rebuilding from whatever
is currently downloadable.

No API key. These are plain public files; the DNC API is unsuitable for bulk
work because past the first page it serves a years-old backfill block, and its
sort parameter does not compose with paging.

A browser User-Agent is required: the site returns 403 to a default urllib
agent. That is the site's bot filtering, not an authentication step.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.request

PAGE = "https://www.ftc.gov/policy-notices/open-government/data-sets/do-not-call-data"
BASE = "https://www.ftc.gov/sites/default/files/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
NAME = re.compile(r"DNC_Complaint_Numbers_[0-9-]+\.csv")


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="ftcdata")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    page = get(PAGE).decode("utf-8", "replace")
    names = sorted(set(NAME.findall(page)))
    if not names:
        print("No daily files found - the page layout may have changed.", file=sys.stderr)
        return 1

    fetched = skipped = 0
    for name in names:
        dest = os.path.join(args.out, name)
        # Already on disk is not the same as already counted: the builder's own
        # content-hash state decides that. This only avoids re-downloading.
        if os.path.exists(dest):
            skipped += 1
            continue
        try:
            data = get(BASE + name)
        except Exception as e:  # noqa: BLE001
            print(f"  {name}: {type(e).__name__}, skipping", file=sys.stderr)
            continue
        with open(dest, "wb") as fh:
            fh.write(data)
        fetched += 1
        print(f"  fetched {name} ({len(data):,} bytes)")

    print(f"\n{len(names)} listed, {fetched} downloaded, {skipped} already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
