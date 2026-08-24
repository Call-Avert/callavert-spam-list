#!/usr/bin/env python3
"""Compile FTC Do Not Call complaint data into the list Call Avert downloads.

    python build_spam_list.py --input "ftcdata/*.csv" --out current.csv.gz

Run it on a schedule and publish the output to static hosting. The app fetches
that one file; it never talks to the FTC and never sends anything anywhere.

WHY A COMPILE STEP AT ALL

  * Size. The raw feed is every complaint. What the app needs is the far
    shorter list of numbers reported often enough to be worth blocking.
  * Fragility. When the FTC changes a column name, this script breaks and you
    fix it once. Phones parsing the raw feed would all break at once, and the
    only cure would be an app update.
  * Portability. Swapping to a commercial feed later means rewriting the reader
    and publishing the same output format. The app does not change at all.

THE HISTORY FILE — why a number is not forgotten every week

  Early versions rebuilt from whatever daily files happened to be on disk, so a
  number that went quiet for a fortnight vanished even though the operation
  behind it was still running. That is far too quick to forgive a spammer.

  A running history is kept instead: every number ever seen, with when it was
  first and last reported and how many reports it has accumulated ACROSS ALL
  RUNS. A number leaves the list only after [--retain-days] with no new report
  at all — not because it missed a window.

  Practically: a number reported 3 times in March and never again is dropped in
  June. A number reported occasionally for a year stays listed the whole year.

  The history file is a working file. It is not shipped and not published.

WHY NOT KEEP NUMBERS FOREVER

  The FCC requires carriers to age a disconnected number at least 45 days
  before giving it to a new subscriber. A number abandoned by a spammer can
  therefore end up belonging to an ordinary person, and blocking it would block
  them. Retention is the dial that trades catching a returning spammer against
  eventually blocking a stranger; the default is deliberately well past 45 days
  but not unbounded.

OUTPUT FORMAT (gzipped, one record per line)

    +14155551234,47,Warranties & Protection Plans

Lines beginning '#' are comments, ignored by the app.
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime
import glob
import gzip
import hashlib
import io
import os
import re
import sys

# A number needs at least this many reports, in total across all runs, before
# it is listed.
#
# The single most important number in this file. FTC data is UNVERIFIED
# consumer reports and caller ID is routinely spoofed, so a number can appear
# because a scammer forged it - the owner being an innocent party who would
# then be blocked. One report is noise. Repeated reports from different people
# are signal.
DEFAULT_MIN_REPORTS = 3

# Days of silence before a number is dropped. Well beyond the FCC's 45-day
# reassignment floor is risky; well under it forgets spammers who pause.
DEFAULT_RETAIN_DAYS = 120

NUMBER_COLUMNS = (
    "Company_Phone_Number", "company_phone_number",
    "Company Phone Number", "phone_number",
)
SUBJECT_COLUMNS = ("Subject", "subject", "Violation_Type")
DATE_COLUMNS = ("Created_Date", "created_date", "created-date")

DIGITS = re.compile(r"\D")
HISTORY_FIELDS = ["number", "first_seen", "last_seen", "reports", "subject"]

# Sidecar next to the history: which source files have already been counted.
#
# WITHOUT THIS THE HISTORY DOUBLE-COUNTS. The FTC publishes a rolling window of
# about 24 daily files, so any scheduled job re-downloads days it has already
# ingested. Counting them again inflated every number's total on every run -
# observed directly: a second pass over the same 24 files turned 227,003
# numbers with one report each into 227,003 numbers with two, so every one of
# them crossed a 2-report threshold. The list would have quietly become
# "everything ever reported once", which is the exact outcome the threshold
# exists to prevent, and nothing would have looked broken.
#
# Files are identified by content hash, not name: the FTC revises a day's file
# in place when late reports arrive, and a revised file SHOULD be re-read.
STATE_SUFFIX = ".sources"


def load_sources(history_path: str) -> set[str]:
    try:
        with io.open(history_path + STATE_SUFFIX, encoding="utf-8") as fh:
            return {line.strip() for line in fh if line.strip()}
    except FileNotFoundError:
        return set()


def save_sources(history_path: str, seen: set[str]) -> None:
    with io.open(history_path + STATE_SUFFIX, "w", encoding="utf-8", newline="\n") as fh:
        for h in sorted(seen):
            fh.write(h + "\n")


def file_fingerprint(path: str) -> str:
    """Content hash plus basename, so a revised file is re-read but an
    unchanged one is skipped however it was named on disk."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return f"{os.path.basename(path)}:{h.hexdigest()[:16]}"


def to_e164(raw: str) -> str | None:
    """Normalise to +1XXXXXXXXXX, or None if it cannot be one.

    Must agree with PhoneNumberNormalizer on the device: a list entry stored in
    a different shape than screening looks up is a row that can never match,
    and nothing would report the mismatch.
    """
    digits = DIGITS.sub("", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    # NANP: area code and exchange both start 2-9.
    if digits[0] in "01" or digits[3] in "01":
        return None
    return "+1" + digits


def pick(header: list[str], candidates: tuple[str, ...], kind: str) -> str | None:
    for name in candidates:
        if name in header:
            return name
    if kind == "phone number":
        raise SystemExit(f"No {kind} column found. Looked for {candidates}, got {header}")
    return None


def load_history(path: str) -> dict[str, dict]:
    try:
        with io.open(path, encoding="utf-8", newline="") as fh:
            return {
                r["number"]: {
                    "first_seen": r["first_seen"],
                    "last_seen": r["last_seen"],
                    "reports": int(r["reports"]),
                    "subject": r.get("subject", ""),
                }
                for r in csv.DictReader(fh)
            }
    except FileNotFoundError:
        return {}


def save_history(path: str, hist: dict[str, dict]) -> None:
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HISTORY_FIELDS)
        w.writeheader()
        for number, h in sorted(hist.items()):
            w.writerow({"number": number, **h})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="glob of FTC CSV files")
    ap.add_argument("--out", default="current.csv.gz")
    ap.add_argument("--history", default="history.csv",
                    help="running record of every number ever seen; not published")
    ap.add_argument("--min-reports", type=int, default=DEFAULT_MIN_REPORTS)
    ap.add_argument("--retain-days", type=int, default=DEFAULT_RETAIN_DAYS,
                    help="drop a number after this many days with no new report")
    ap.add_argument("--today", default=None, help="override today's date (YYYY-MM-DD)")
    args = ap.parse_args()

    today = (datetime.date.fromisoformat(args.today) if args.today
             else datetime.date.today())

    paths = sorted(glob.glob(args.input))
    if not paths:
        raise SystemExit(f"No input files matched {args.input!r}")

    hist = load_history(args.history)
    known_before = len(hist)
    seen_sources = load_sources(args.history)

    new_reports = 0
    unusable = 0
    skipped_files = 0
    subjects: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)

    for path in paths:
        fingerprint = file_fingerprint(path)
        if fingerprint in seen_sources:
            skipped_files += 1
            continue
        seen_sources.add(fingerprint)

        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                raise SystemExit(f"{path} has no header row")
            header = list(reader.fieldnames)
            ncol = pick(header, NUMBER_COLUMNS, "phone number")
            scol = pick(header, SUBJECT_COLUMNS, "subject")
            dcol = pick(header, DATE_COLUMNS, "date")

            for row in reader:
                number = to_e164(row.get(ncol, ""))
                if number is None:
                    unusable += 1
                    continue
                seen = (row.get(dcol) or "")[:10] if dcol else ""
                if len(seen) != 10 or not seen.startswith("2"):
                    seen = today.isoformat()

                h = hist.get(number)
                if h is None:
                    hist[number] = {"first_seen": seen, "last_seen": seen,
                                    "reports": 1, "subject": ""}
                else:
                    h["reports"] += 1
                    if seen < h["first_seen"]:
                        h["first_seen"] = seen
                    if seen > h["last_seen"]:
                        h["last_seen"] = seen
                new_reports += 1

                if scol:
                    subject = (row.get(scol) or "").strip()
                    if subject:
                        subjects[number][subject] += 1

    # Most-reported subject wins, blending what history already knew.
    for number, counter in subjects.items():
        hist[number]["subject"] = counter.most_common(1)[0][0]

    # Expire on SILENCE, not on falling out of whatever window was fed in.
    cutoff = (today - datetime.timedelta(days=args.retain_days)).isoformat()
    expired = [n for n, h in hist.items() if h["last_seen"] < cutoff]
    for n in expired:
        del hist[n]

    listed = [(n, h) for n, h in hist.items() if h["reports"] >= args.min_reports]
    listed.sort(key=lambda kv: (-kv[1]["reports"], kv[0]))

    with gzip.open(args.out, "wt", encoding="utf-8", newline="\n") as out:
        out.write("# Call Avert spam list\n")
        out.write(f"# built {today.isoformat()} from FTC Do Not Call complaint data\n")
        out.write(f"# listed at {args.min_reports}+ reports; dropped after "
                  f"{args.retain_days} days with no new report\n")
        for number, h in listed:
            out.write(f"{number},{h['reports']},{h['subject']}\n")

    save_history(args.history, hist)
    save_sources(args.history, seen_sources)

    print(f"input        {len(paths):>4} file(s), {skipped_files} already counted, "
          f"{new_reports:,} new usable reports ({unusable:,} unusable)")
    print(f"history      {known_before:>9,} numbers known before -> {len(hist):,} now")
    print(f"expired      {len(expired):>9,} numbers silent for {args.retain_days}+ days")
    print(f"LISTED       {len(listed):>9,} numbers with {args.min_reports}+ reports")
    if not listed:
        print("\nNOTHING LISTED - publishing this would wipe every user's list.",
              file=sys.stderr)
        return 1
    print(f"wrote        {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
