# Final submission

[Download the submission ZIP](abundant-take-home-sheil-sarda-20260914.zip).

The ZIP contains exactly the four requested roots:

- `samples/`: Rerun chunk optimizer, corrected Zenoh timestamp instrumentation and Burn PyTorch checkpoint reader v4.
- `jobs/`: 27 captured trials for those retained task cohorts, with Harbor's original directory structure and file bytes.
- `archive/`: 34 built task directories and revisions excluded from the submitted samples.
- `report/`: the user's finalized `Take home challenge results - Sheil Sarda 091426.pdf`, copied without editing or re-exporting.

The archive is 579,642,121 bytes (about 580 MB). Its SHA-256 is `93239c8c0a8e3049930f905b8fbd66e3718652831e4a5b30993f68908e072665`.

`submission-verification.json` records the completed build and verifies all 5,581 file hashes and sizes. The report is the sole file under `report/`; the ZIP contains no PowerPoint files. `pdf-review.json` records the read-only check of the 21-page PDF. All 36 supplied take-home files still match commit `c1ae968`.

`build-submission.py` copies the previously verified `review-pack-99-002.zip`, appends the user's PDF, and checks the resulting contents against the frozen plan and report hash. It preserves the earlier packages. It refuses to overwrite an existing completed or partial output. The previous supporting-evidence ZIP remains separate and is not nested into this submission.

The user confirmed submission on September 14, 2026. The upload was performed by the user; the archive and PDF above identify the prepared submission.
