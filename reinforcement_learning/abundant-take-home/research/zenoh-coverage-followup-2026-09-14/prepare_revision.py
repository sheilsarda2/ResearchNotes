"""Create a validation-only revision after the untouched-reference gap is proven.

Does not modify or promote the v3 task. Requires a completed focused diagnostic
showing the exact admin assertion failure; no reference change happens earlier.
"""
import sys
sys.dont_write_bytecode = True
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
ORIGINAL = ROOT / 'research/task-revisions/rs-zenoh-timestamp-instrumentation-v3'
REVISION = BASE / 'rs-zenoh-timestamp-instrumentation-v4-validation'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes(root):
    return {str(path.relative_to(root)): digest(path)
            for path in sorted(root.rglob('*')) if path.is_file()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reference-result', type=Path, required=True)
    parser.add_argument('--reference-failure-log', type=Path, required=True)
    parser.add_argument('--reference-correction', type=Path, required=True)
    args = parser.parse_args()
    assert not REVISION.exists(), 'Never overwrite a validation revision'
    result = json.loads(args.reference_result.read_text())
    run = args.reference_result.parent
    inputs = json.loads((run / 'inputs.json').read_text())
    assert result['status'] == 'complete' and result['case_exit_code'] == 101
    assert result['model_calls'] == 0 and result['reward'] is None
    assert result['container_absent'] and result['claim_absent'] and result['inputs_unchanged']
    assert inputs['source'] == 'gold' and inputs['patch_sha256'] is None
    assert inputs['task_files'] == hashes(ORIGINAL)
    assert result['inputs_sha256'] == digest(run / 'inputs.json')
    assert args.reference_failure_log.resolve() == (run / 'case/cargo.stdout.log').resolve()
    assert result['artifact_sha256']['cargo.stdout.log'] == digest(args.reference_failure_log)
    gold_manifest = BASE / 'mutants/gold-source-files.json'
    assert json.loads((run / 'case/effective-source-manifest.json').read_text()) == json.loads(gold_manifest.read_text())
    for folder, name in [('codec-test', 'timestamp_robustness.rs'),
                         ('admin-test', 'timestamp_adminspace.rs'),
                         ('admin-test', 'timestamp_adminspace_reply_stack.rs')]:
        assert inputs['tests'][name] == digest(BASE / folder / name)
    log = args.reference_failure_log.read_text()
    parser_spec = importlib.util.spec_from_file_location('reference_score_parser', ORIGINAL / 'tests/score.py')
    score_parser = importlib.util.module_from_spec(parser_spec)
    parser_spec.loader.exec_module(score_parser)
    binaries = score_parser.parse_run(log)
    assert binaries['timestamp_adminspace_reply_stack']['tests']['adminspace_reply_preserves_received_query_stack'] == 'FAILED'
    assert 'admin reply must inherit the instrumented query stack as received by the admin handler' in log
    assert 'error[E' not in log, 'Reference diagnostic must reach the actual runtime assertion'
    for name in ('decoder_accepts_valid_boundary_counts_without_changing_payload',
                 'decoder_rejects_zero_configuration',
                 'decoder_rejects_record_count_above_255',
                 'unknown_point_and_bad_uhlc_are_skipped_without_dropping_publication',
                 'receive_appends_at_254_and_drops_further_records_at_255',
                 'adminspace_query_invokes_receive_timestamp_callback'):
        assert f'test {name} ... ok' in log, f'Other new behavior needs investigation: {name}'
    correction = args.reference_correction.read_text()
    assert correction.startswith('diff --git a/zenoh/src/net/runtime/adminspace.rs b/zenoh/src/net/runtime/adminspace.rs\n')
    assert correction.count('diff --git ') == 1
    original = hashes(ORIGINAL)
    shutil.copytree(ORIGINAL, REVISION)
    for folder, name in [('codec-test', 'timestamp_robustness.rs'),
                         ('admin-test', 'timestamp_adminspace.rs'),
                         ('admin-test', 'timestamp_adminspace_reply_stack.rs')]:
        shutil.copy2(BASE / folder / name, REVISION / 'tests/hidden/zenoh/tests' / name)
    (REVISION / 'solution/admin-reply-stack.patch').write_text(correction)
    solve = REVISION / 'solution/solve.sh'
    old = solve.read_text()
    needle = 'git apply /solution/changes.patch\n'
    assert old.count(needle) == 1
    solve.write_text(old.replace(needle, needle +
        '# Follow-up correction: preserve the admin query stack when constructing its reply.\n'
        'git apply --check /solution/admin-reply-stack.patch\n'
        'git apply /solution/admin-reply-stack.patch\n'))
    verifier = REVISION / 'tests/test.sh'
    old = verifier.read_text()
    updated = old.replace('hidden_pr_tests interop_gold',
                          'hidden_pr_tests robustness_tests admin_timestamp_tests interop_gold')
    copy_at = 'cp "$HIDDEN/zenoh/tests/timestamp_instrumentation.rs" "$BUILD/zenoh/tests/"\n'
    assert old.count(copy_at) == 1
    updated = updated.replace(copy_at, copy_at + ''.join(
        f'cp "$HIDDEN/zenoh/tests/{name}.rs" "$BUILD/zenoh/tests/"\n'
        for name in ('timestamp_robustness', 'timestamp_adminspace', 'timestamp_adminspace_reply_stack')))
    build_at = '  --test timestamp_instrumentation --test ts_interop "${REGRESSION_TARGETS[@]}" \\\n'
    assert old.count(build_at) == 1
    updated = updated.replace(build_at,
        '  --test timestamp_instrumentation --test timestamp_robustness \\\n'
        '  --test timestamp_adminspace --test timestamp_adminspace_reply_stack \\\n'
        '  --test ts_interop "${REGRESSION_TARGETS[@]}" \\\n')
    run_at = '# ---------------------------------------------------------------- differential interop\n'
    assert old.count(run_at) == 1
    updated = updated.replace(run_at,
        '# Follow-up contracts 6 and 7; every original group is still required.\n'
        'run_group robustness_tests 180 --expect "timestamp_robustness=5" -- \\\n'
        '  "${PKG_A[@]}" -- --test timestamp_robustness -- --test-threads=1\n'
        'run_group admin_timestamp_tests 180 --expect "timestamp_adminspace=1" \\\n'
        '    --expect "timestamp_adminspace_reply_stack=1" -- \\\n'
        '  "${PKG_A[@]}" -- --test timestamp_adminspace --test timestamp_adminspace_reply_stack -- --test-threads=1\n\n'
        + run_at)
    verifier.write_text(updated)
    # The copied historical provenance remains intact; this status identifies its scope.
    (REVISION / 'STATUS.md').write_text(
        '# Validation-only follow-up; not promoted\n\n'
        'Validation results are recorded outside this frozen task in ../revision.json. '
        'This status file does not certify validation or promotion. This separate revision adds '
        'contract 6/7 tests and repairs the demonstrated admin reply-stack omission in the '
        'reference solution. The instruction, resources, existing verifier groups and v3 '
        'campaign remain unchanged. Copied provenance describes the parent v3 task, not a '
        'validation result for this revision. See ../revision.json for exact lineage.\n')
    after = hashes(REVISION)
    assert hashes(ORIGINAL) == original
    assert after['instruction.md'] == original['instruction.md']
    assert after['task.toml'] == original['task.toml']
    record = {'schema_version': 1, 'status': 'created_validation_pending',
              'created_at': datetime.now(timezone.utc).isoformat(), 'model_calls': 0,
              'parent_task': str(ORIGINAL.relative_to(ROOT)), 'parent_file_sha256': original,
              'revision_task': str(REVISION.relative_to(ROOT)), 'revision_file_sha256': after,
              'changed_files': sorted(name for name in after if original.get(name) != after[name]),
              'deleted_files': sorted(set(original) - set(after)),
              'reference_failure_log': str(args.reference_failure_log.resolve().relative_to(ROOT)),
              'reference_failure_log_sha256': digest(args.reference_failure_log),
              'reference_result': str(args.reference_result.resolve().relative_to(ROOT)),
              'reference_result_sha256': digest(args.reference_result),
              'derived_gold_source_manifest_sha256': digest(gold_manifest),
              'reference_correction_sha256': digest(args.reference_correction),
              'preparer_sha256': digest(Path(__file__)),
              'instruction_and_budgets_unchanged': True,
              'gold_interop_peer_unchanged': True,
              'existing_v3_scores_and_campaign_unchanged': True,
              'promoted': False}
    (BASE / 'revision.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'revision': record['revision_task'], 'changed_files': record['changed_files']}))


if __name__ == '__main__':
    main()
