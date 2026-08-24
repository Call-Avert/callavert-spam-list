#!/usr/bin/env python3
"""Tests for build_spam_list.py.

    python -m unittest discover -s tools -p "test_*.py"

This script decides which phone numbers get blocked on other people's phones,
and it runs unattended on a schedule. A silent mistake here does not look like
a crash - it looks like a list that is quietly too small (nobody is protected)
or quietly too large (strangers are blocked). Both have happened during
development, neither raised an error, and both were caught by eye rather than
by anything automatic. Hence these.

The cases that matter most are the retention boundaries: an off-by-one there
is the difference between releasing a reassigned number on time and blocking
somebody's new line for an extra day.
"""

from __future__ import annotations

import gzip
import importlib.util
import io
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "build_spam_list", os.path.join(_HERE, "build_spam_list.py"))
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)

HEADER = "Company_Phone_Number,Subject,Created_Date\n"


def write_csv(path: str, rows: list[tuple[str, str, str]]) -> None:
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(HEADER)
        for number, subject, date in rows:
            fh.write(f"{number},{subject},{date}\n")


class BuilderCase(unittest.TestCase):
    """Runs the real main() against real files in a temp directory."""

    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()
        self.history = os.path.join(self.dir, "history.csv")
        self.out = os.path.join(self.dir, "out.csv.gz")

    def build(self, today: str, glob: str = "*.csv", **flags) -> dict[str, int]:
        """Run the compiler; return {number: reports} as published."""
        argv = ["build_spam_list.py",
                "--input", os.path.join(self.dir, glob),
                "--out", self.out,
                "--history", self.history,
                "--today", today]
        for key, value in flags.items():
            argv += ["--" + key.replace("_", "-"), str(value)]
        old = sys.argv
        sys.argv = argv
        try:
            # main() returns 1 and prints when nothing is listed; that is a
            # legitimate outcome to assert on, not an exception.
            self.rc = build.main()
        finally:
            sys.argv = old
        published = {}
        with gzip.open(self.out, "rt", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("#") or not line.strip():
                    continue
                number, reports, _subject = line.rstrip("\n").split(",", 2)
                published[number] = int(reports)
        return published


class TestNormalisation(unittest.TestCase):
    """to_e164 must agree with PhoneNumberNormalizer on the device.

    A number stored in one shape and looked up in another is a row that can
    never match, and nothing anywhere would report the mismatch.
    """

    def test_ten_digit_gets_plus_one(self):
        self.assertEqual(build.to_e164("4155551234"), "+14155551234")

    def test_leading_one_is_country_code_not_an_eleventh_digit(self):
        self.assertEqual(build.to_e164("14155551234"), "+14155551234")

    def test_formatting_is_stripped(self):
        self.assertEqual(build.to_e164("(415) 555-1234"), "+14155551234")
        self.assertEqual(build.to_e164("415.555.1234"), "+14155551234")
        self.assertEqual(build.to_e164("+1 415 555 1234"), "+14155551234")

    def test_nanp_shape_is_enforced(self):
        # Area code and exchange must both start 2-9; these cannot be dialled
        # and would be dead weight in the list.
        self.assertIsNone(build.to_e164("0155551234"))
        self.assertIsNone(build.to_e164("1155551234"))
        self.assertIsNone(build.to_e164("4150551234"))
        self.assertIsNone(build.to_e164("4151551234"))

    def test_wrong_length_and_junk_are_rejected(self):
        for junk in ("", "555", "41555512345678", "not a number", None):
            self.assertIsNone(build.to_e164(junk))


class TestRetentionTiers(BuilderCase):
    """How long a number is kept, by how many people reported it.

    Retention is inclusive of the cutoff day: a number last reported exactly
    `retain` days ago survives, and is dropped the following day.
    """

    def test_one_report_survives_to_the_boundary(self):
        write_csv(os.path.join(self.dir, "a.csv"),
                  [("4155551234", "Scam", "2026-08-09")])
        published = self.build("2026-08-23")  # exactly 14 days later
        self.assertIn("+14155551234", published)

    def test_one_report_drops_one_day_past_the_boundary(self):
        write_csv(os.path.join(self.dir, "a.csv"),
                  [("4155551234", "Scam", "2026-08-08")])
        published = self.build("2026-08-23")  # 15 days later
        self.assertNotIn("+14155551234", published)

    def test_two_reports_outlive_the_one_report_window(self):
        # Same date that expires a lone report; corroboration keeps it.
        write_csv(os.path.join(self.dir, "a.csv"),
                  [("4155551234", "Scam", "2026-08-01"),
                   ("4155551234", "Scam", "2026-08-01")])
        published = self.build("2026-08-23")
        self.assertEqual(published.get("+14155551234"), 2)

    def test_two_reports_drop_past_forty_five_days(self):
        write_csv(os.path.join(self.dir, "a.csv"),
                  [("4155551234", "Scam", "2026-06-01"),
                   ("4155551234", "Scam", "2026-06-01")])
        published = self.build("2026-08-23")
        self.assertNotIn("+14155551234", published)

    def test_three_reports_survive_where_two_would_not(self):
        write_csv(os.path.join(self.dir, "a.csv"),
                  [("4155551234", "Scam", "2026-06-01")] * 3)
        published = self.build("2026-08-23")
        self.assertEqual(published.get("+14155551234"), 3)

    def test_three_reports_still_drop_past_full_retention(self):
        # Past 120 days a spammer's abandoned number may belong to a stranger.
        write_csv(os.path.join(self.dir, "a.csv"),
                  [("4155551234", "Scam", "2026-01-01")] * 3)
        published = self.build("2026-08-23")
        self.assertNotIn("+14155551234", published)

    def test_clock_runs_from_the_last_report_not_the_first(self):
        # Reported long ago and again recently: the recent report is what
        # matters, or a long-running spammer would age out mid-campaign.
        write_csv(os.path.join(self.dir, "a.csv"),
                  [("4155551234", "Scam", "2026-01-01"),
                   ("4155551234", "Scam", "2026-08-20")])
        published = self.build("2026-08-23")
        self.assertEqual(published.get("+14155551234"), 2)

    def test_tier_flags_are_honoured(self):
        write_csv(os.path.join(self.dir, "a.csv"),
                  [("4155551234", "Scam", "2026-08-09")])
        published = self.build("2026-08-23", retain_1=7)
        self.assertNotIn("+14155551234", published)


class TestPromotion(BuilderCase):
    """A second report must lengthen an existing number's leash."""

    def test_second_report_in_a_later_run_promotes_the_tier(self):
        write_csv(os.path.join(self.dir, "a.csv"),
                  [("4155551234", "Scam", "2026-08-01")])
        self.build("2026-08-05")

        # 20 days after the first report, a lone report would already be gone.
        write_csv(os.path.join(self.dir, "b.csv"),
                  [("4155551234", "Scam", "2026-08-21")])
        published = self.build("2026-08-23")
        self.assertEqual(published.get("+14155551234"), 2)

    def test_a_number_expired_in_an_earlier_run_starts_over(self):
        write_csv(os.path.join(self.dir, "a.csv"),
                  [("4155551234", "Scam", "2026-08-01")])
        self.build("2026-08-05")
        # Run past its retention with nothing new: it leaves history.
        write_csv(os.path.join(self.dir, "b.csv"),
                  [("2125559999", "Scam", "2026-09-01")])
        published = self.build("2026-09-02")
        self.assertNotIn("+14155551234", published)


class TestCountOnce(BuilderCase):
    """Each day's reports must be counted exactly once, ever.

    This is the failure that turned 227,003 one-report numbers into 227,003
    two-report numbers on a second pass, quietly listing the entire feed. It
    raised no error - the run looked entirely normal.
    """

    def daily(self, date: str, rows) -> str:
        """A file named the way the FTC names them."""
        path = os.path.join(self.dir, f"DNC_Complaint_Numbers_{date}.csv")
        write_csv(path, rows)
        return path

    def test_same_file_twice_does_not_double_count(self):
        self.daily("2026-08-20", [("4155551234", "Scam", "2026-08-20")])
        first = self.build("2026-08-23")
        second = self.build("2026-08-23")
        self.assertEqual(first.get("+14155551234"), 1)
        self.assertEqual(second.get("+14155551234"), 1)

    def test_a_revised_file_does_not_recount_what_it_already_gave(self):
        # The FTC edits a day's file in place when late reports arrive. Its
        # rows carry no report ID, so a re-read cannot tell an already-counted
        # row from a new one - it would count BOTH again. Skipping the revision
        # under-counts by the late reports; re-reading it over-counts by every
        # row the two versions share. Under-counting is the safe direction: it
        # can only shorten how long a number stays listed, never extend it.
        self.daily("2026-08-20", [("4155551234", "Scam", "2026-08-20")])
        self.build("2026-08-23")
        self.daily("2026-08-20", [("4155551234", "Scam", "2026-08-20"),
                                  ("4155551234", "Scam", "2026-08-20")])
        published = self.build("2026-08-23")
        self.assertEqual(published.get("+14155551234"), 1)

    def test_a_different_day_is_counted_even_if_identical(self):
        rows = [("4155551234", "Scam", "2026-08-20")]
        self.daily("2026-08-19", rows)
        self.build("2026-08-23", glob="*2026-08-19.csv")
        self.daily("2026-08-20", rows)
        published = self.build("2026-08-23", glob="*2026-08-20.csv")
        self.assertEqual(published.get("+14155551234"), 2)

    def test_republishing_a_day_under_a_new_name_does_not_double_count(self):
        rows = [("4155551234", "Scam", "2026-08-20")]
        self.daily("2026-08-20", rows)
        self.build("2026-08-23")
        write_csv(os.path.join(self.dir, "DNC_2026-08-20_revised.csv"), rows)
        published = self.build("2026-08-23")
        self.assertEqual(published.get("+14155551234"), 1)

    def test_undated_files_fall_back_to_content_so_reruns_are_idempotent(self):
        rows = [("4155551234", "Scam", "2026-08-20")]
        write_csv(os.path.join(self.dir, "adhoc.csv"), rows)
        self.build("2026-08-23")
        published = self.build("2026-08-23")
        self.assertEqual(published.get("+14155551234"), 1)

    def test_state_written_by_an_older_version_is_still_honoured(self):
        # Upgrading the key must not orphan the state file and recount the
        # whole window. Legacy entries were "basename:hash".
        path = self.daily("2026-08-20", [("4155551234", "Scam", "2026-08-20")])
        self.build("2026-08-23")
        with io.open(self.history + ".sources", "w",
                     encoding="utf-8", newline="\n") as fh:
            fh.write(os.path.basename(path) + ":deadbeefdeadbeef\n")
        published = self.build("2026-08-23")
        self.assertEqual(published.get("+14155551234"), 1)


class TestFloorAndOutput(BuilderCase):

    def test_history_in_the_input_glob_is_not_parsed_as_ftc_data(self):
        # "data/*.csv" happily matches history.csv if it sits alongside.
        write_csv(os.path.join(self.dir, "a.csv"),
                  [("4155551234", "Scam", "2026-08-20")])
        self.history = os.path.join(self.dir, "history.csv")
        published = self.build("2026-08-23")
        self.assertEqual(published.get("+14155551234"), 1)
        published = self.build("2026-08-23")
        self.assertEqual(published.get("+14155551234"), 1)

    def test_min_reports_floor_excludes_below_it(self):
        write_csv(os.path.join(self.dir, "a.csv"),
                  [("4155551234", "Scam", "2026-08-20"),
                   ("2125559999", "Scam", "2026-08-20"),
                   ("2125559999", "Scam", "2026-08-20")])
        published = self.build("2026-08-23", min_reports=2)
        self.assertNotIn("+14155551234", published)
        self.assertIn("+12125559999", published)

    def test_empty_result_reports_failure_rather_than_publishing_silence(self):
        # Publishing an empty list would wipe every user's copy.
        write_csv(os.path.join(self.dir, "a.csv"),
                  [("4155551234", "Scam", "2026-01-01")])
        self.build("2026-08-23")
        self.assertEqual(self.rc, 1)

    def test_most_reported_subject_wins(self):
        write_csv(os.path.join(self.dir, "a.csv"),
                  [("4155551234", "Warranties", "2026-08-20"),
                   ("4155551234", "Warranties", "2026-08-20"),
                   ("4155551234", "Medical", "2026-08-20")])
        self.build("2026-08-23")
        with gzip.open(self.out, "rt", encoding="utf-8") as fh:
            body = [l for l in fh if not l.startswith("#")]
        self.assertIn("Warranties", body[0])

    def test_output_is_sorted_most_reported_first(self):
        write_csv(os.path.join(self.dir, "a.csv"),
                  [("4155551234", "Scam", "2026-08-20")]
                  + [("2125559999", "Scam", "2026-08-20")] * 3)
        self.build("2026-08-23")
        with gzip.open(self.out, "rt", encoding="utf-8") as fh:
            body = [l for l in fh if not l.startswith("#")]
        self.assertTrue(body[0].startswith("+12125559999"))

    def test_unusable_rows_do_not_stop_the_build(self):
        write_csv(os.path.join(self.dir, "a.csv"),
                  [("", "Scam", "2026-08-20"),
                   ("junk", "Scam", "2026-08-20"),
                   ("4155551234", "Scam", "2026-08-20")])
        published = self.build("2026-08-23")
        self.assertEqual(list(published), ["+14155551234"])

    def test_missing_date_falls_back_to_today_rather_than_being_dropped(self):
        write_csv(os.path.join(self.dir, "a.csv"),
                  [("4155551234", "Scam", "")])
        published = self.build("2026-08-23")
        self.assertIn("+14155551234", published)


if __name__ == "__main__":
    unittest.main(verbosity=2)
