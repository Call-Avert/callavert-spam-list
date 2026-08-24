# Call Avert spam list

The list of reported spam numbers that the
[Call Avert](https://play.google.com/store/apps/details?id=com.callavert.app)
Android app downloads.

**This repository contains data only.** No application source code lives here.

## What is in it

`spam/current.csv.gz` — gzipped, one record per line:

```
+12134742799,3,Other
+12179224455,2,Calls pretending to be government, businesses, or family and friends
```

`number,reportCount,subject`. Lines starting with `#` are comments. The subject
may itself contain commas; consumers split on the first two only.

## Where it comes from

Compiled from the **FTC Do Not Call complaint data**, a public record the
Federal Trade Commission publishes each weekday.

The FTC states plainly that its complaint data is *unverified*, and caller ID is
routinely spoofed, so a number reported once may belong to an innocent person
whose number was forged rather than to a spammer.

**How long a number stays listed depends on how many people reported it.**

| Reports | Listed for | Reasoning |
| --- | --- | --- |
| 1 | 14 days | Caught while probably still active, released quickly if wrong |
| 2 | 45 days | Corroborated by a second stranger; sits at the FCC reassignment floor |
| 3 or more | 120 days | A pattern rather than a coincidence |

The clock runs from the **most recent** report, so a number that keeps being
reported keeps resetting it and stays listed while it is still in use. A second
report promotes a number to the longer window immediately.

This replaced a flat rule that simply discarded anything under three reports —
93% of the feed — which meant missing a spammer during exactly the days they
were dialling. Expressing confidence as *time* rather than as exclusion keeps
that coverage without treating one unverified report as proof.

## How the app uses it

The app **downloads this file** and matches callers against its own copy, on the
device. It never sends a phone number, a contact or any call record anywhere.
Blocking keeps working with the phone offline.

That is the opposite of the usual caller-ID model, which asks a server about
every incoming call.

## Licence

FTC complaint data is a work of the United States government and is in the
public domain. This compilation is published under
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) — use it freely.

## How it stays current

A GitHub Action rebuilds this file **every Monday at 06:00 UTC**
(`.github/workflows/rebuild-list.yml`). It downloads the FTC's daily complaint
files, folds them into a running history, and commits the result.

Nothing about the app or its users is involved. The Action reads public FTC
data and writes one public file.

### Why there is a history

The FTC keeps only a rolling window of about 24 daily files. Rebuilding from
whatever is currently downloadable would drop a number the moment it aged out
of that window, even if the operation behind it was still running. The history
remembers every number, its first and last report and its running total, and a
number leaves the list only after **120 days with no new report at all**.

That 120 days is not arbitrary: the FCC requires carriers to age a disconnected
number at least 45 days before reassigning it, so a long-abandoned spam number
can end up belonging to an ordinary person. Retention trades catching a
returning spammer against eventually blocking a stranger.

The history is kept as a release asset, not committed — it grows to tens of
megabytes and nobody needs its past versions.

### Two guards worth knowing about

**Each day's reports are counted once, ever.** The builder records which days
it has already consumed. Without that, every weekly run would re-count the
overlapping days and inflate every number's total until the whole feed crossed
the threshold — a failure that produces a *larger*, entirely plausible-looking
list rather than an error. It happened, and nothing looked wrong.

Days are the key rather than filenames or content hashes, because an FTC row
carries no report ID and so cannot be recognised as one already counted. Keying
on the name would double-count a day republished under a new one; keying on
content would re-read a file the FTC revises with late reports and count every
row the two versions share a second time. Keying on the day means late reports
are missed — a small under-count, and the safe direction, since it can only
shorten how long a number is listed.

**A shrinking list is refused.** The Action will not publish fewer than 500
numbers, nor fewer than half of what is already published. The proportional
check matters more: a regression that loses most of the list still leaves
thousands of numbers, so a fixed floor would wave it through while most users
silently lost most of their protection. A bad publish cannot be recalled once
devices have fetched it.

**The compiler is tested before it runs.** `tools/test_build_spam_list.py`
covers the retention boundaries, tier promotion and the count-once rule, and CI
runs it before building anything. Both bugs this list has had published
cleanly — a bad list looks exactly like a good one.
