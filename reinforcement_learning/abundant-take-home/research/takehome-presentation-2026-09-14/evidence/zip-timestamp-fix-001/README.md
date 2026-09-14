# ZIP timestamp boundary repair

The only production change adds `strict_timestamps=False` to `zip_verified`'s `ZipFile` constructor. Python clamps ZIP header dates below 1980 or above 2107 to the supported endpoints. Read-back timestamps use DOS two-second precision; the upper endpoint reads as 2107-12-31 23:59:58.

Source files, copied files, original nanosecond mtimes, payload hashes, raw paths and all packaging gates are unchanged. The copied tree and captured manifests retain original mtimes; ZIP headers cannot express them exactly outside the DOS range. No assembly was launched. Prior plan001, mapping, partial review-pack001 and its failed ZIP remain historical; use a new plan/output identity for later assembly.

The unchanged implementation reproduced four errors among five new tests; the ordinary-date case passed. After the one-line fix, all five pass, including epoch, pre1980, post2107, normal DOS rounding and empty directories. File tests verify source and copied SHA-256/mtime_ns before and after ZIP creation and read every ZIP payload. All 54 existing packager/snapshot/revision tests also pass (59 total). These are local Python 3.9.6 tests with temporary fixtures; no model, container, campaign or raw-trial replay occurred.

`before/`, `after/`, `changes.diff` and `verification.json` bind the exact sources and logs. Original 36 supplied files were verified unchanged before and after. Independent source review found no blocker. This changes only timestamp handling; it does not broaden the strict three-sample/27-job pack or interpret expanded report coverage as task promotion.
