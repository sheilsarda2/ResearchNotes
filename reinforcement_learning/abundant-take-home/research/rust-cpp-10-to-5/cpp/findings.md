# C++ candidate review — September 13, 2026

Research only. No Harbor package, reference implementation, oracle/no-op validation, or model difficulty measurement was produced. Work stopped on the coordinator's instruction after three current-source reproductions. All new files are under this directory. Source checkouts are unmodified.

## Result

| Candidate | Current execution | Demand for exact behavior | Engineering-task fit |
|---|---|---|---|
| range-v3 `nth_element` partition safety, [#1737](https://github.com/ericniebler/range-v3/issues/1737), corroborated by [#1723](https://github.com/ericniebler/range-v3/issues/1723) | Exact attached 2,000-double input reports ASan stack-buffer-underflow on current default branch | Strong: reporter's production top-10 selection; independent report on 10,000 integers and a later 13-integer reproducer | Credible bug; natural fix may be compact control flow. Working controls still need local execution. |
| RapidJSON required/dependency names and `additionalProperties`, [#2296](https://github.com/Tencent/rapidjson/issues/2296) | Invalid object accepted with `additionalProperties:false`; schema-valued additional-properties type check also bypassed. Four controls pass. | Medium: author reports real schema validation mismatch and follows up with precise tests; no named production application or multiple independent affected users established | Strongest completed evidence in this lane. Schema compilation, presence tracking, and validation dispatch interact; still possibly a small repair. |
| CLI11 nested help with required parent positional, [#1450](https://github.com/CLIUtils/CLI11/issues/1450), previously [#1268](https://github.com/CLIUtils/CLI11/issues/1268) | Child help command prints parent's help on current default branch | Medium: same reporter returned after purported earlier fix and supplied standalone CLI reproducer | Potential parser integration task. Help bypass must preserve ordinary parsing and positional/subcommand ambiguity. Working controls still need local execution. |

If selecting solely from this lane now, prioritize RapidJSON for a fully controlled failure and range-v3 for stronger exact demand. CLI11 is a reserve. None is established as a >75-agent-step task; do not inflate scope to satisfy that requirement. These are candidates for comparison, not three automatic additions to the final five.

## Source pins and executable evidence

Fresh shallow checkouts of default branches were obtained during this review:

| Repository | Commit | Local source |
|---|---|---|
| `ericniebler/range-v3` | `108f93c279c8f9cec175dac361084983d0176e99` | `range-v3/` |
| `Tencent/rapidjson` | `24b5e7a8b27f42fa16b96fc70aade9106cf7102f` | `rapidjson/` |
| `CLIUtils/CLI11` | `c1cfe00d2f3d862aecfe6e69ec810414d5f4c906` | `CLI11/` |

All probes compiled and ran on macOS ARM64 with host Apple `clang++`, C++17. They do not establish Linux Harbor behavior. Exact commands and provenance are in [observed.json](observed.json). Builds took seconds and require only upstream headers plus the standard library. Source archives, Linux toolchain pins, independent protected tests and offline verifier packaging remain future work.

### range-v3: unsafe partition restart

The reporter uses `ranges::nth_element` to select the ten highest values from 2,000 doubles, including positive infinity. They report the regression after a dependency update, with production use explicitly described in the issue comments. This is a valid ordered domain: no NaNs are involved. A maintainer initially confused infinity with NaN and corrected that distinction. A separate user in #1723 reports a similar failure with repeated integer values; this broadens evidence beyond floating-point semantics without inventing a separate task.

The exact [reporter attachment](https://github.com/ericniebler/range-v3/files/9626637/RangesCrashExample.txt) is preserved as [range-nth-upstream.cpp](range-nth-upstream.cpp). On current source, ASan reports an eight-byte read before the stack array, with `nth_element.hpp:219` in the stack. See [range-nth-upstream.log](range-nth-upstream.log). This independently reproduces invalid access, though the source line differs from the original report's predecrement line. The exact process return code was not separately captured because the shell command printed the saved log afterward; the saved ASan report explicitly ends with `ABORTING`.

**Controls and alternatives:** #1723 reports that replacing the call with `std::ranges::nth_element` avoids the crash. The reporter supplies a small integer reproducer in comments. Neither this alternative nor all-equal, finite-only, sorted/reversed, comparator/projection, or iterator/sentinel controls were executed locally before the stop instruction. Do not present them as locally passing.

The current source contains `first = i; continue;` inside an inner guard-search loop after partitioning an equal-valued prefix. That suggests a compact loop-restart repair, not a demonstrated long-horizon implementation. A fair eventual verifier should check partition correctness, multiset preservation, repeated values, infinities, projections and iterator overload behavior. Do not prescribe one implementation or add unrelated range algorithms.

### RapidJSON: membership rules confused with required-presence bookkeeping

The issue demonstrates that a required name absent from `properties` and `patternProperties` is accepted despite `additionalProperties:false`. A follow-up by the same author reproduces this in the upstream Draft-4 schema tests and points out that required names share the internal property table with declared properties. This is concrete demand for the exact failure, but application impact is not independently quantified.

Our [rapid-required.cpp](rapid-required.cpp) uses the real `SchemaDocument` and `SchemaValidator` with six deterministic fixtures. See [rapid-required.log](rapid-required.log):

| Fixture | Expected | Observed |
|---|---|---|
| Required `C`, undeclared, additional properties forbidden | Reject | **Accept** |
| Required `C` missing | Reject | Reject |
| Required `B` and `C` both declared | Accept | Accept |
| Required `C` covered by `patternProperties` | Accept | Accept |
| Ordinary undeclared `C`, not listed in required | Reject | Reject |
| Required undeclared `C` is string but additional-properties schema requires number | Reject | **Accept** |

The second failure is a closely related consequence of the same dispatch path. It should not be described as separately reported demand. The program prints observations and exits zero; this is not an assert-based grader. Source review shows the shared table also gathers dependency source/target names, but those cases have not been executed. Current `Key` dispatch returns immediately upon any table hit, bypassing additional-property handling. A future task should preserve required and dependency presence tracking while enforcing whether a property is actually declared or pattern-matched. Avoid changing Draft-4 schema semantics based merely on the issue author's informal wording: an unsatisfiable schema is allowed; the required result is rejection of violating instances, not mandatory rejection of the schema itself.

**Controls and alternatives:** Four controls above were locally executed. Reporter comparisons include other validators, but we did not execute those systems. No equivalent correction was found in the bounded public search below. The known older [PR #977](https://github.com/Tencent/rapidjson/pull/977) concerns pattern/additional-properties assertions, while current source still exhibits this different acceptance failure. Active [PR #2377](https://github.com/Tencent/rapidjson/pull/2377) concerns non-string dependency entries and is nearby implementation work, not a located fix for #2296. Natural repair size remains unmeasured and may be modest.

### CLI11: choosing the right command's help without a required parent value

The reporter first raised this in #1268, reported continued failure in version 2.6.2, then opened #1450 because they could not reopen the older issue. The exact hierarchy is `subc1 ID subc2`, where `ID` is a required integer positional. The desired invocation `subc1 subc2 --help` should select the nested command's help without requiring a real object ID.

The [standalone reporter probe](cli-help.cpp), compiled against current source, prints `First subcommand` and the parent `ID INT REQUIRED` block. It does not print the nested option `--flag`. See [cli-help.log](cli-help.log). Process exit is zero, so exit-only validation would miss the bug. Current source `_parse_subcommand` prioritizes outstanding required positionals before dispatching a recognized subcommand; any change needs care around intentional positional values matching subcommand names.

**Controls and alternatives:** In the issue, `subc1 1 subc2 --help` prints `Nested subcommand` and `--flag`; this is an existing usable workaround. That passing control, ordinary successful parsing, missing-ID failure without help, and custom/short help flags were not executed locally before stopping. [PR #1292](https://github.com/CLIUtils/CLI11/pull/1292), “find options through fallthrough,” is the published prior work linked to #1268. The fresh failing reproduction shows that this history does not establish the reported behavior as fixed. No later exact active fix was found in the bounded search. The desired help behavior is reporter-supported, but no fresh maintainer acceptance of a particular parser policy was found; ambiguity policy needs explicit design when constructing the task.

## Bounded overlap review

Timestamped GitHub API records are saved under [sources/](sources/). The review read each candidate issue, all returned comments, and its timeline, including #1723 and #1268. It inspected all open PRs returned across pages (22 range-v3, 116 RapidJSON, 22 CLI11), 100 most recently updated closed PRs per repository, and 50 recent commits per repository. It also queried PRs for `nth_element`, `additionalProperties`, and `positional help`, plus issue references `1737`, `1723`, `2296`, and `1450`. No exact current implementation was located for the three failures. This does not prove absence from every unreferenced fork or private branch.

`overlap-verified-*.json` and `overlap-reference-*.json` contain successful search results. Earlier `overlap-search-*.json` contain HTTP 403 rate-limit errors and are **not evidence of no results**. The initial closed-PR lists sorted oldest-first; use the later `*-recent-closed-prs.json` files for the actual recent-100 review. General web search had irrelevant results and did not establish additional evidence.

Other concrete exclusions found while screening: spdlog daily retention [#2553](https://github.com/gabime/spdlog/issues/2553) has exact [PR #3598](https://github.com/gabime/spdlog/pull/3598); yaml-cpp trailing scalar [#819](https://github.com/jbeder/yaml-cpp/issues/819) has [#1458](https://github.com/jbeder/yaml-cpp/pull/1458); yaml-cpp null spelling [#1290](https://github.com/jbeder/yaml-cpp/issues/1290) has [#1460](https://github.com/jbeder/yaml-cpp/pull/1460); nlohmann destructor [#5135](https://github.com/nlohmann/json/issues/5135) has [#5239](https://github.com/nlohmann/json/pull/5239) and earlier [#4654](https://github.com/nlohmann/json/pull/4654); nlohmann locale [#5198](https://github.com/nlohmann/json/issues/5198) has [#5237](https://github.com/nlohmann/json/pull/5237); range-v3 stable-sort corruption [#1848](https://github.com/ericniebler/range-v3/issues/1848) has public [#1865](https://github.com/ericniebler/range-v3/pull/1865). They were not padded into the candidate pool.

## Assimp broader lead: unqualified, no build launched

[Assimp #5234](https://github.com/assimp/assimp/issues/5234) has strong exact interoperability demand: LeoCAD DAE exports load incorrectly through F3D/Assimp, another viewer user confirms invalid vertices, and an Assimp maintainer acknowledges a DAE-loader issue. The thread contains downloadable input files and reports that equivalent OBJ/3DS files load correctly. A later comment reports `Not enough data for accessor`. Those observations are upstream reports, not our current-code reproduction.

A sparse current checkout is at `assimp/`, commit `d3714526e658cab55e171e0548bc4cf30c230ac8`. The reporter archive is `collada-reporter.tar.gz`. COLLADA-only CMake configuration was attempted under `assimp-build/` with host Clang and tests/exporters disabled, but failed because the sparse checkout omitted `cmake-modules/`. See `assimp-configure.log`. **No compilation or import experiment was started.** The coordinator then explicitly stopped further experiments. No process remains running from this attempt.

Searches for PR references to `5234`, `Collada accessor`, and `leocad` found no exact correction; that was only an initial search and does not qualify the lead. Assimp should not displace an executed candidate until current import behavior, valid-file semantics, matched controls, and deeper overlap checks are completed. Other broader leads had blocking evidence: RocksDB #10007 has an explicit maintainer implementation claim; Assimp #4557 has a public work claim and expired input link; #6361 has only a private failing model; DuckDB #24994 is reported by a maintainer as passing on current main, and #21592 has published later fix/test history.
