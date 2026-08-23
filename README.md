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
