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
Federal Trade Commission publishes each weekday. A number is listed only after
it has been reported by several different people — the FTC states plainly that
its complaint data is *unverified*, and caller ID is routinely spoofed, so a
number reported once or twice is as likely to belong to an innocent person whose
number was forged as it is to a spammer.

The threshold is the safeguard, and it is deliberately conservative.

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

**Files are counted once.** The builder records the content hash of every file
it has consumed. Without that, each weekly run would re-count the overlapping
days and inflate every number's total until the whole feed crossed the
threshold — a failure that produces a *larger*, plausible-looking list rather
than an error. A file the FTC revises with late reports hashes differently and
is re-read.

**A small list is refused.** The Action will not publish fewer than 500
numbers. A bad publish cannot be recalled once devices have fetched it.
