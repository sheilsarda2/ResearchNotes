# Mark usage incomplete when a query has no saved response

Mini 2.4.6 increments its logical query counter before `model.query()` and
appends the assistant response and cost after the query returns. The Fable
Luigi trial `844YtMQ` terminated with `BadGatewayError`: its native trace records
21 queries but only 20 assistant responses. Previously the evidence summary
treated its observed token and cost totals as uncensored because the native
trace and cost field existed.

The exporter now sets the two existing usage/billing completeness flags when
a recorded integer query count exceeds the saved response count. It preserves
every observed numeric total. This does not infer the number of internal HTTP
retries, assert that an additional charge occurred, or estimate its amount.
Missing or malformed counters do not invent an unanswered query.

The evidence and abnormal-exit suites passed all 54 tests in Harbor's Python
environment. New cases cover a terminal 21-query/20-response trace, recovered
retries with matching counts, malformed counters, and input preservation.
Root review confirmed the production change only extends the existing censor
predicate. `preservation-proof.json` binds the source/tests and a read-only
recollection of both affected Luigi terminal records.

Fable's provider failure was already classified as infrastructure and excluded
from valid-trial counts. Sonnet's guarded timeout remains a counted timeout
under the existing policy. Neither classification nor reward changes. The
saved historical evidence, raw trials, and all 36 supplied take-home files
remain unchanged. The comparison permits only the three previously added
provenance fields and Fable's two corrected completeness flags.

No healthy workers were restarted. New imports and fresh derived collections
use this fix; already imported exporters may retain the older flags. Final
analysis must use a separate derived audit when correcting historical metadata,
preserving Harbor's original outputs for delivery.
