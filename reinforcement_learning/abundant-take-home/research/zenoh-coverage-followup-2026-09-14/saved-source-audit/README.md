# Saved Zenoh v3 source audit

Sonnet/max `gSYDMEG` (626 turns) and Fable/max `QYtUfZA` (216 turns) both contain code for the flagged 255-record cap, zero-configuration/count rejection, malformed-record skipping, and admin Receive path. No straightforward omission was found. This is source inspection, not executed verifier proof.

The implementations differ: Sonnet retains raw records in the codec and filters invalid points/UHLC timestamps when creating the public stack; Fable filters them in the codec. Fair tests should check documented public outcomes without requiring either internal representation. Sonnet's bounded `u8` count reader rejects values above255 through the shared integer codec. Both admin paths record Receive before creating the delivered Query.

`audit.json` binds source paths and line references, all16 inspected-file hashes to the saved benchmark input snapshots, and original result/score provenance. It records the limits of this review. The existing quality-review gap remains unresolved by source inspection alone. No tests, replays, model calls, task edits, or score changes were made.
