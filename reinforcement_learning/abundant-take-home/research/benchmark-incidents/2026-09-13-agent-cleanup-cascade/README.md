# Cleanup failure cancelled sibling trials

At 2026-09-13 21:54 UTC, cpp-zenoh-cpp-connectivity-api__znS7CQo ended with
agent exit 137 and unconfirmed process cleanup. Its last printed command used
`pkill -9 -f zenohd`. The launcher and its execution guard carried task text
containing that name in their process arguments, making self-termination a
plausible trigger; the precise signal target is not recorded. No coincident
watchdog action or VM OOM event was found.

The cancellation cascade is confirmed independently: the cleanup error was
raised again during Harbor output recovery, escaped Trial.run, and cancelled
eleven sibling coroutines in the job TaskGroup. None of these twelve trials
reached a verifier. Their original result, deadline, and evidence files are
bound in incident.json. They are invalid infrastructure outcomes, not task
successes or failures, and all recorded usage remains potentially incomplete.

The repair must keep task text out of launch arguments and contain an escaped
cleanup error only after finalization and confirmed container teardown. Grading
must remain blocked for the failed trial. Once those changes pass no-model
regressions, a hash-bound resolution proof can mark this exact incident as
resolved for admission health checks. New failures receive no exemption.

The two runtime fixes now pass 69 combined unit/integration tests, five real
Docker cases, and eight real mini-swe-agent CLI checks with offline factories.
`resolution-proof.json` binds those proofs and their source hashes. The incident
registry resolves only these twelve unchanged trials for admission health;
future cleanup failures receive no historical exemption. A 30-second grace
period lets a qualified teardown marker arrive after finalization, and cache
fingerprints make later marker or incident-proof changes visible to the monitor.
