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

RETENTION IS TIERED BY HOW MANY PEOPLE REPORTED IT

  How long a number is kept depends on how well corroborated it is, because the
  cost of being wrong differs so much between the two ends. A number reported
  by one person gets a fortnight; by two, 45 days; by three or more, the full
  retention. See RETAIN_BY_REPORTS.

  This replaces an all-or-nothing threshold that deleted every number with
  fewer than three reports - which was most of the feed. A short leash is a
  better answer than a blindfold: it catches the number while it is likely
  still dialling, and lets go quickly if the report was wrong.

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

# The floor: a number needs at least this many reports, in total across all
# runs, before it is listed at all.
#
# This was 3, and it carried the entire risk judgement on its own - one report
# was treated as pure noise and discarded forever. That is a blunt trade. FTC
# data is UNVERIFIED and caller ID is routinely spoofed, so a lone report may
# well be a scammer forging an innocent party's number. But 93% of reported
# numbers have exactly one report, and throwing all of them away means missing
# a spammer during the very window they are active.
#
# Confidence is now expressed as TIME rather than as exclusion (see
# RETAIN_BY_REPORTS), so the floor can sit at 1 without treating a single
# unverified report as though it were proven.
DEFAULT_MIN_REPORTS = 1

# How long a number stays listed after its LAST report, keyed by how many
# people reported it. Confidence and dwell time are tied together on purpose.
#
#   1 report    14 days  Caught while it is probably still live, and released
#                        quickly if it was a spoofed innocent line. Two weeks
#                        of wrongly blocking somebody is a real cost, but a
#                        bounded and short one.
#   2 reports   45 days  Corroborated by a second stranger. Lands on the FCC's
#                        reassignment floor, so the number is very unlikely to
#                        have been handed to anyone new yet.
#   3+ reports  full     A pattern, not a coincidence: DEFAULT_RETAIN_DAYS.
#
# The clock runs from the LAST report, not the first, so a number that keeps
# being reported keeps resetting its own timer and never ages out while it is
# still in use. A one-report number that draws a second report is promoted to
# the longer tier at once, dated from that newer report.
RETAIN_BY_REPORTS = {1: 14, 2: 45}

# Days of silence before a well-corroborated (3+) number is dropped. Well
# beyond the FCC's 45-day reassignment floor is risky; well under it forgets
# spammers who pause.
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
# Files are identified by the DAY they cover, taken from the filename.
#
# Neither obvious alternative is safe, because an FTC row carries no report ID
# and so cannot be recognised as one already counted:
#
#   By name alone - a day republished under a new name is counted twice.
#   By content    - the FTC revises a day's file in place when late reports
#                   arrive. Re-reading the revision counts every row it shares
#                   with the version already ingested, inflating those numbers
#                   a second time.
#
# Keying on the day is exact: each day contributes exactly once, whatever the
# file is called and however often it is revised or re-downloaded. The cost is
# that reports added to a day after it was first ingested are missed. That is a
# small, bounded UNDER-count, and under-counting is the safe direction - it can
# only shorten how long a number stays listed, never extend it. A persistent
# caller shows up on later days regardless.
#
# Files with no recognisable date fall back to a content hash, which at least
# makes a straight re-run idempotent.
STATE_SUFFIX = ".sources"
FILE_DATE = re.compile(r"(20\d{2}-\d{2}-\d{2})")


def load_sources(history_path: str) -> set[str]:
    """Read the already-counted set, upgrading entries written by older runs.

    Without the upgrade, the first run after a key change treats every file it
    has already ingested as new and doubles every count in the current window -
    precisely the bug this state file exists to prevent.
    """
    try:
        with io.open(history_path + STATE_SUFFIX, encoding="utf-8") as fh:
            raw = [line.strip() for line in fh if line.strip()]
    except FileNotFoundError:
        return set()

    seen = set()
    for entry in raw:
        if entry.startswith("day:") or entry.startswith("sha:"):
            seen.add(entry)
            continue
        # Legacy "basename:hash". The basename carries the date we now key on.
        found = FILE_DATE.search(entry)
        seen.add(f"day:{found.group(1)}" if found
                 else "sha:" + entry.rsplit(":", 1)[-1])
    return seen


def save_sources(history_path: str, seen: set[str]) -> None:
    with io.open(history_path + STATE_SUFFIX, "w", encoding="utf-8", newline="\n") as fh:
        for h in sorted(seen):
            fh.write(h + "\n")


def file_fingerprint(path: str) -> str:
    """Identify which day's reports this file carries. See STATE_SUFFIX."""
    found = FILE_DATE.search(os.path.basename(path))
    if found:
        return f"day:{found.group(1)}"
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha:" + h.hexdigest()[:32]


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
                    help="days of silence before a 3+ report number is dropped")
    ap.add_argument("--retain-1", type=int, default=RETAIN_BY_REPORTS[1],
                    help="days of silence before a ONE-report number is dropped")
    ap.add_argument("--retain-2", type=int, default=RETAIN_BY_REPORTS[2],
                    help="days of silence before a TWO-report number is dropped")
    ap.add_argument("--today", default=None, help="override today's date (YYYY-MM-DD)")
    args = ap.parse_args()

    today = (datetime.date.fromisoformat(args.today) if args.today
             else datetime.date.today())

    # A glob such as "data/*.csv" will happily match the history file if it
    # sits in the same directory, and the history would then be parsed as if
    # it were FTC input. Exclude our own working files by identity, not by
    # name, so a relative and an absolute path to the same file both match.
    ours = set()
    for candidate in (args.history, args.history + STATE_SUFFIX, args.out):
        try:
            ours.add(os.path.realpath(candidate))
        except OSError:
            pass
    paths = [p for p in sorted(glob.glob(args.input))
             if os.path.realpath(p) not in ours]
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

    # Expire on SILENCE, not on falling out of whatever window was fed in - and
    # how much silence is forgiven depends on how well corroborated the number
    # is. One report buys a fortnight; three or more buys four months.
    #
    # Bucketed rather than computed per number so this stays a single dict
    # lookup per entry over a history running to hundreds of thousands of rows.
    cutoffs = {
        1: (today - datetime.timedelta(days=args.retain_1)).isoformat(),
        2: (today - datetime.timedelta(days=args.retain_2)).isoformat(),
        3: (today - datetime.timedelta(days=args.retain_days)).isoformat(),
    }
    expired = [n for n, h in hist.items()
               if h["last_seen"] < cutoffs[min(h["reports"], 3)]]
    for n in expired:
        del hist[n]

    tiers = collections.Counter(min(h["reports"], 3) for h in hist.values())

    listed = [(n, h) for n, h in hist.items() if h["reports"] >= args.min_reports]
    listed.sort(key=lambda kv: (-kv[1]["reports"], kv[0]))

    with gzip.open(args.out, "wt", encoding="utf-8", newline="\n") as out:
        out.write("# Call Avert spam list\n")
        out.write(f"# built {today.isoformat()} from FTC Do Not Call complaint data\n")
        out.write(f"# listed at {args.min_reports}+ reports\n")
        out.write(f"# dropped after {args.retain_1}d silent (1 report), "
                  f"{args.retain_2}d (2), {args.retain_days}d (3+)\n")
        for number, h in listed:
            out.write(f"{number},{h['reports']},{h['subject']}\n")

    save_history(args.history, hist)
    save_sources(args.history, seen_sources)

    print(f"input        {len(paths):>4} file(s), {skipped_files} already counted, "
          f"{new_reports:,} new usable reports ({unusable:,} unusable)")
    print(f"history      {known_before:>9,} numbers known before -> {len(hist):,} now")
    print(f"expired      {len(expired):>9,} numbers past their retention")
    print(f"  tier 1      {tiers[1]:>9,} one report    (kept {args.retain_1}d)")
    print(f"  tier 2      {tiers[2]:>9,} two reports   (kept {args.retain_2}d)")
    print(f"  tier 3      {tiers[3]:>9,} three or more (kept {args.retain_days}d)")
    print(f"LISTED       {len(listed):>9,} numbers with {args.min_reports}+ reports")
    if not listed:
        print("\nNOTHING LISTED - publishing this would wipe every user's list.",
              file=sys.stderr)
        return 1
    print(f"wrote        {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
