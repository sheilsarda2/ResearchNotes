# Final review of persistent dispatch rounds

The shared admission lock now records primary task/model/effort dispatches.
An ahead-of-round cell waits until every unfinished cell reaches its round.
Explicit repair jobs retain the resource gates but do not advance primary
dispatch counters. The shared limit remains 12; successful-trial accounting,
task inputs, rewards, and the 2,340-trial target are unchanged.

The initial live rollout exposed a missing-file read that escaped the cache
and canceled 13 sibling trials. The cache now retains its previous snapshot
or waits for a readable state file. The exact affected results are registered
as infrastructure interruptions; their raw rewards and evidence are retained.
The three incident manifests reference separately archived runner logs.

Task-selection refresh reads the user plan under the same shared lock used by
promotion, preventing an old read from restoring a retired revision. Refresh
also preserves revision history and other policy metadata. Three standalone
regressions cover the race and preservation behavior. The exact additional
patch and its proof are in the adjacent watchdog-fix folder.

The first combined test run caught an incident-registry schema regression:
a list-shaped registry raised an uncaught AttributeError. The incident reader
now validates its shape at the existing supervisor review boundary. The
failed first run is retained, and the corrected combined suite passes all
50 tests. A separate disposable live-worker smoke test confirms that the
existing fake task and process survive admission installation while eligible
cells advance and ahead cells wait. No model calls were made by these checks.

Source hashes are recorded before and after verification. No live campaign
was changed by this review. Existing workers retain their installed code;
the final selection-refresh and schema checks apply when their corresponding
modules are next loaded. The already activated watchdog round-wait fix is
committed separately as 8cf7b26.

The actual working runner used during these checks also contains a preexisting
job-lock cache hook. That separate experiment remains outside this scheduler
commit; its two lines and helper are preserved in the working tree.
