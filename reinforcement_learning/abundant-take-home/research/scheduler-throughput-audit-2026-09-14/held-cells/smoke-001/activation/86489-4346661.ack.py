import asyncio
def _benchmark_apply_interleaving():
    import gc, hashlib, json, os, pathlib, runpy, sys, time
    _expected = {'pid': 86489, 'identity': '4346661', 'job': 'job', 'shared': '/tmp/held-cell-smoke-w3g4ex2y/shared.json', 'root': '/workspaces/sheil_research/reinforcement_learning/abundant-take-home', 'ack': '/workspaces/sheil_research/reinforcement_learning/abundant-take-home/research/scheduler-throughput-audit-2026-09-14/held-cells/smoke-001/activation/86489-4346661.ack.json', 'source_sha256': {'scripts/benchmark_interleaving.py': '576b96c70ea6609b22674254be28199339f3585b99c93f85bf748508c0338326', 'scripts/benchmark_shared_admission.py': 'f6e2cee75437899f4c65eea299c38100b6140e5ed3be23d1074b7dfddc4384b6', 'scripts/harbor-resource-runner.py': '7f00c308bd679609b8855fc31387ab5acdcc2641a5e39027af74d65da10ed4d9'}, 'runtime_proof': False}
    _ack = pathlib.Path(_expected['ack'])
    _result = {'pid': os.getpid(), 'started_at': time.time(), 'passed': False}
    try:
        from benchmark_shared_admission import process_identity, SharedAdmission
        assert os.getpid() == _expected['pid']
        assert process_identity(os.getpid()) == _expected['identity']
        root = pathlib.Path(_expected['root'])
        for name, digest in _expected['source_sha256'].items():
            assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
        from harbor.job import Job
        import __main__
        Admission = __main__.Admission
        jobs = [obj for obj in gc.get_objects() if isinstance(obj, Job)
                and obj.config.job_name == _expected['job']]
        admissions = [obj for obj in gc.get_objects() if isinstance(obj, Admission)
                      and obj.shared and str(obj.shared.path.resolve()) == _expected['shared']]
        assert len(jobs) == len(admissions) == 1
        admission = admissions[0]
        _result['active_before'] = sorted(admission.active)
        if _expected.get('runtime_proof'):
            from benchmark_mini_tool_runtime import install as install_runtime
            install_runtime()
            _result['reviewed_runtime_installed_for_future_agents'] = True
        import benchmark_interleaving as rounds
        # Refresh functions in the original module namespace, retaining its live
        # registry and existing acquire closures. Reloading would lose queue state.
        import ast
        source = root / 'scripts/benchmark_interleaving.py'
        tree = ast.parse(source.read_text())
        functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), 'exec'), rounds.__dict__)
        from benchmark_interleaving import install, register
        new_method = runpy.run_path(str(root / 'scripts/benchmark_shared_admission.py'))['SharedAdmission'].try_acquire
        # Old acquire frames call this method on each poll, so they also see the fix.
        SharedAdmission.try_acquire = new_method
        install(Admission, admission.shared)
        _result['registered_configs'] = register(jobs[0], admission.shared)
        import benchmark_interleaving as rounds
        _result['registered_names'] = len(rounds.TRIALS)
        _result['module_id'] = id(rounds)
        _result['active_after'] = sorted(admission.active)
        assert _result['active_after'] == _result['active_before']
        _result.update(passed=True, source_sha256=_expected['source_sha256'],
                       identity=_expected['identity'], job=_expected['job'])
    except BaseException as error:
        _result['error_type'] = type(error).__name__
    finally:
        _result['finished_at'] = time.time()
        temporary = _ack.with_suffix('.tmp')
        temporary.write_text(json.dumps(_result, indent=2) + '\n')
        temporary.replace(_ack)

asyncio.get_running_loop().call_soon(_benchmark_apply_interleaving)
