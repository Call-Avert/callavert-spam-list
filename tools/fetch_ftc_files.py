#!/usr/bin/env python3
"""Download the FTC's daily Do Not Call complaint files.

    python fetch_ftc_files.py --out ftcdata

The FTC links about 24 of these from its data-sets page - roughly a month. Older
files stay served after they stop being linked, so --backfill-days walks back by
date to reach them. That matters because retention runs to 120 days: without the
walk the 2- and 3-report tiers could only fill at one day per day, and the list
sat at about a third of its true size.

Fetching the whole retention window every run also means the build reproduces
itself from the files alone, with no state carried between runs.

No API key. These are plain public files; the DNC API is unsuitable for bulk
work because past the first page it serves a years-old backfill block, and its
sort parameter does not compose with paging.

A browser User-Agent is required: the site returns 403 to a default urllib
agent. That is the site's bot filtering, not an authentication step.
"""

from __future__ import annotations

import argparse
import datetime
import glob
import os
import re
import sys
import time
import urllib.error
import urllib.request

PAGE = "https://www.ftc.gov/policy-notices/open-government/data-sets/do-not-call-data"
BASE = "https://www.ftc.gov/sites/default/files/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
NAME = re.compile(r"DNC_Complaint_Numbers_[0-9-]+\.csv")

# The page lists only the last few weeks - about 24 weekday files, roughly a
# month. Older files are still served, they just stop being linked, so a date
# walk reaches history the page will not show us.
#
# This matters because retention runs to 120 days for well-corroborated
# numbers. Without a backfill those tiers can only fill at one day per day: the
# list would have taken four months to reach its true size, while sitting at
# about a third of it.
FILENAME = "DNC_Complaint_Numbers_%s.csv"

# Be a polite guest on a government file server.
PAUSE_SECONDS = 0.4


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="ftcdata")
    ap.add_argument("--backfill-days", type=int, default=0,
                    help="also walk back this many days by URL, past what the "
                         "page still lists (weekends are skipped)")
    ap.add_argument("--keep-days", type=int, default=0,
                    help="delete files in --out older than this many days "
                         "(0 keeps everything)")
    ap.add_argument("--today", default=None, help="override today (YYYY-MM-DD)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    page = get(PAGE).decode("utf-8", "replace")
    names = set(NAME.findall(page))
    if not names:
        print("No daily files found - the page layout may have changed.", file=sys.stderr)
        return 1
    print(f"{len(names)} file(s) listed on the page")

    if args.backfill_days > 0:
        today = (datetime.date.fromisoformat(args.today) if args.today
                 else datetime.date.today())
        added = 0
        for back in range(args.backfill_days + 1):
            day = today - datetime.timedelta(days=back)
            if day.weekday() >= 5:      # the FTC publishes on weekdays only
                continue
            name = FILENAME % day.isoformat()
            if name not in names:
                names.add(name)
                added += 1
        print(f"{added} older weekday(s) added by date walk")

    names = sorted(names)

    fetched = skipped = 0
    missing: list[str] = []
    for name in names:
        dest = os.path.join(args.out, name)
        # Already on disk is not the same as already counted: the builder's own
        # content-hash state decides that. This only avoids re-downloading.
        if os.path.exists(dest):
            skipped += 1
            continue
        try:
            data = get(BASE + name)
        except urllib.error.HTTPError as e:
            # A 404 on a walked-back date is ordinary: a holiday, or simply
            # older than the FTC keeps. Only say so for files the page itself
            # advertised, where a miss means something actually changed.
            if e.code != 404:
                print(f"  {name}: HTTP {e.code}, skipping", file=sys.stderr)
            missing.append(name)
            continue
        except Exception as e:  # noqa: BLE001
            print(f"  {name}: {type(e).__name__}, skipping", file=sys.stderr)
            missing.append(name)
            continue
        time.sleep(PAUSE_SECONDS)
        with open(dest, "wb") as fh:
            fh.write(data)
        fetched += 1
        print(f"  fetched {name} ({len(data):,} bytes)")

    if args.keep_days > 0:
        # Left alone, a cached download directory grows for ever and every run
        # parses more files to reach the same answer - the builder would expire
        # those numbers regardless. Prune by the date in the name rather than
        # by mtime, which a cache restore rewrites to the time of the restore.
        today = (datetime.date.fromisoformat(args.today) if args.today
                 else datetime.date.today())
        floor = (today - datetime.timedelta(days=args.keep_days)).isoformat()
        removed = 0
        for path in glob.glob(os.path.join(args.out, "*.csv")):
            found = re.search(r"(20\d{2}-\d{2}-\d{2})", os.path.basename(path))
            if found and found.group(1) < floor:
                os.remove(path)
                removed += 1
        if removed:
            print(f"pruned {removed} file(s) older than {floor}")

    print(f"\n{len(names)} wanted, {fetched} downloaded, {skipped} already "
          f"present, {len(missing)} not available")
    if fetched == 0 and skipped == 0:
        print("Nothing was obtained at all.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
