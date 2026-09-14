# Terminal adoption review for the Luigi streaming canary

This read-only review describes a possible future adoption route. No plan,
control, runtime, process, score, or collector was changed, and no adoption was
performed. [source-hash-receipt.json](source-hash-receipt.json) binds the eight
reviewed source files, current plans, observed process identities and predeclared
transport eligibility policy.

At **2026-09-14 08:37:57 UTC**, the main supervisor was PID12518/start2748497,
its existing Harbor runner was PID98349/start2185934 with five claims, and its
watchdog was PID97135/start4355546. All identities were checked through `/proc`;
the runner also matched its shared registration. Recheck these identities before
any future action. All36 supplied files remained unchanged.

The supported route is conditional:

1. Wait for both the canary's sole raw trial and its **outer job** to have
   terminal `result.json` records. Require its custom runner, claims and
   containers absent, completed verifier/cleanup evidence, and independent
   validation against the predeclared transport policy. Reward0 and reward1
   receive the same eligibility treatment; valid timeouts follow existing
   classification, while infrastructure/provenance failures remain separate.
2. Revalidate task checksum, delivered instruction, model/effort and budgets.
   Declare the original raw job/config path and config hash, a specific preserved
   infrastructure `replacement_for`, a reason, and the hash-bound transport
   certificate. The Foxglove declaration at main plan lines450–455 supplies the
   existing format. Do not rename or rewrite raw trial files.
3. Use a short, owned, field-only pause of **main new admissions**, preserving
   unrelated control fields. Await a fresh paused summary from the exact current
   supervisor before changing the plan. This establishes that its earlier
   unpaused iteration finished and prevents its repair-generation branch from
   overwriting the appended declaration. Recheck the plan hash and atomically
   append only the terminal job.
4. Terminate only the exact supervisor PID, never its process group, runner or
   watchdog. Let the existing watchdog be the sole replacement launcher. Verify
   the new supervisor loaded the appended job, existing runner/trial identities
   survived, and the terminal canary was not scheduled. Restore only the owned
   pause fields, preserving any intervening foreign or health pause.

The supervisor loads its plan once and takes an exclusive campaign lock
(`scripts/run-candidate-screen.py:294–310`). It has no child-termination signal
handler or `finally` block. Runners are launched with `start_new_session=True`
(`scripts/run-sonnet-confirmation.py:188–202`), and a new supervisor rediscovers
them (`scripts/run-candidate-screen.py:396`). The watchdog uses identity-checked
PID signals and launches the normal supervisor (`scripts/watch-candidate-campaign.py:198–224,319–341`).

The outer-job terminal gate is essential: the custom canary runner is outside
`active_pid`'s recognized executables (`scripts/run-sonnet-confirmation.py:171–185`).
An active or incompletely terminal appended job could therefore enter
`prepare_resume`, which can stop its containers, and resume under the ordinary
runtime (`scripts/run-candidate-screen.py:445–474`,
`scripts/benchmark_recovery.py:67–94`). A truthy outer-job `finished_at` excludes
it through `select_jobs` (`scripts/benchmark_job_scheduling.py:4–10`).

Keep **20 counted trials per cell**, **main1800**, and **aggregate2340**
(main1800 plus three revision campaigns of180 each). Four preserved Luigi
Fable/max infrastructure outcomes currently provide replacement headroom;
recheck all existing/adopted/pending work so projected counted results cannot
exceed20. The supervisor rejects overcounting at
`scripts/run-candidate-screen.py:374–378` and generates later repairs from the
remaining deficit at lines449–461. Do not change dispatch receipts or targets.

The collector requires the declaration and preserves it
(`scripts/collect-takehome-evidence.py:169–174`). It chooses the earliest counted
result by finish timestamp, then raw path, at line276. A prior ordinary result
that finishes first remains selected regardless of reward. Extra transport
fields are retained but not validated by that collector; the separate terminal
certificate and supporting evidence remain necessary. If these gates cannot
be proved, retain the canary as separate evidence and defer adoption.
