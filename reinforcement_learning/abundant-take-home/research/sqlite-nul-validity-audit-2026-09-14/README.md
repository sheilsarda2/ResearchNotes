# SQLite schema-plan: NUL failure validity

The NUL-default requirement is valid. The task says that all strings are literal
default values, with no exclusion for embedded NUL. Rejecting that value does not
satisfy the contract. Earlier working notes calling this a verifier/spec mismatch
were incorrect; a limitation of raw SQL string construction is not a limitation
of the requested behavior.

SQLite permits embedded NUL in stored TEXT, although display and quoting
functions can obscure it. Its DEFAULT clause permits parenthesized constant
expressions. [SQLite NUL documentation](https://www.sqlite.org/nulinstr.html),
[CREATE TABLE documentation](https://www.sqlite.org/lang_createtable.html).

The saved [SQLite 3.53.1 proof](sqlite-default-proof.json), produced by
[sqlite-default-proof.py](sqlite-default-proof.py), confirms that
`DEFAULT (CAST(X'610062' AS TEXT))` round-trips `a\x00b`. Passing a raw NUL byte in
SQL source instead raises `ProgrammingError`. This proves feasibility without
requiring the model to copy the reference implementation's approach.

The recorded score is **0/30 counted attempts**: 21 from the earlier top-five
pilot and nine from the current campaign. These are 29 normal scored completions
and one budgeted agent timeout, across nine model/effort combinations with
repeats. All 30 share task checksum
`9a553a3a42062bfe4341f5eb26df1e1a5ab53681b22b3ab88a7c0d6a10f3d672`.
Five classified infrastructure outcomes and one additional canceled raw pilot
result omitted from its stopped summary are outside the denominator.

All 30 fail `test_default_values_are_literals[a\x00b]`. It is the only failed
assertion in three 331/332 runs:

| Trial | Campaign | Model / effort |
|---|---|---|
| `sqlite-utils-schema-plan__Z8wJqit` | Current | Fable / high |
| `sqlite-utils-schema-plan__MMRrfUn` | Pilot | Opus / high |
| `sqlite-utils-schema-plan__THvQb5m` | Pilot | Fable / medium |

The remaining 27 also fail other assertions. Clear implementation defects exist:
for example, `Lrq7CLw` raises `TypeError` on `types={"v": []}` instead of the
required `TransformError`. Additional contract questions remain separate from
NUL validity: rejection of fullwidth numeric digits (14 observed failures),
numeric digit separators (nine), and a dropped column also appearing in
`column_order` (nine). The wording and expected behavior of these assertions
should be reviewed directly; this audit does not treat reference-code choices as
additional requirements or reclassify those assertions.

[results.json](results.json) contains every counted trial's full path, failed-test
names/messages, outcome, task checksum, and raw-file SHA256 provenance. It also
records excluded trials, captured summary rows, the three separate contract
questions, and proof hashes. Summary files are mutable; the recorded hashes
identify the exact sampled bytes. Raw outcomes, tasks, runtime and controls were
not changed, and no trial was replayed. All 36 supplied files still match
`c1ae968`.
