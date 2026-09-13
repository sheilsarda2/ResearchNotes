"""Add a native, sortable Checks passed (%) column to the installed Harbor viewer."""

from pathlib import Path
import sys


def replace_once(text, old, new, label):
    if text.count(old) != 1:
        raise SystemExit(f"Harbor {label} patch target not found uniquely")
    return text.replace(old, new, 1)


viewer = Path(sys.argv[1])
pending = []
models_path = viewer / "models.py"
models = models_path.read_text()
if "HARBOR_CHECK_PASS_FIELDS" not in models:
    old_fields = """    # HARBOR_FAILED_CHECKS_FIELDS: None distinguishes missing reports from zero.
    n_failed_checks: int | None = None
    n_check_reports: int = 0
"""
    new_fields = """    # HARBOR_CHECK_PASS_FIELDS: None distinguishes missing reports from zero.
    n_failed_checks: int | None = None
    n_passed_checks: int | None = None
    n_total_checks: int = 0
    n_check_reports: int = 0
    checks_pass_pct: float | None = None
"""
    if "HARBOR_FAILED_CHECKS_FIELDS" in models:
        models = replace_once(models, old_fields, new_fields, "existing check fields")
    else:
        anchor = "    n_errored_trials: int = 0\n"
        models = replace_once(models, anchor, anchor + new_fields, "job summary fields")
    pending.append((models_path, models))

server_path = viewer / "server.py"
server = server_path.read_text()
if "HARBOR_CHECK_PASS_SUMMARY" not in server:
    old_summary = """        # HARBOR_FAILED_CHECKS_SUMMARY: read only jobs on the requested page.
        from harbor.viewer.failed_checks import count_failed_checks
        for summary in page_summaries:
            summary.n_failed_checks, summary.n_check_reports = count_failed_checks(
                _validate_job_path(summary.name)
            )
"""
    new_summary = """        # HARBOR_CHECK_PASS_SUMMARY: read only jobs on the requested page.
        from harbor.viewer.failed_checks import summarize_checks
        for summary in page_summaries:
            for field, value in summarize_checks(_validate_job_path(summary.name)).items():
                setattr(summary, field, value)
"""
    start = server.index('    @app.get("/api/jobs",')
    end = server.index('    @app.get("/api/jobs/{job_name}")', start)
    endpoint = server[start:end]
    if "HARBOR_FAILED_CHECKS_SUMMARY" in endpoint:
        endpoint = replace_once(endpoint, old_summary, new_summary, "existing check summary")
    else:
        anchor = "        page_summaries = summaries[start_idx:end_idx]\n"
        endpoint = replace_once(endpoint, anchor, anchor + "\n" + new_summary, "jobs pagination")
    server = server[:start] + endpoint + server[end:]
    pending.append((server_path, server))

bundles = list((viewer / "static/assets").glob("home-*.js"))
if len(bundles) != 1:
    raise SystemExit("Expected one Harbor home bundle")
bundle_path = bundles[0]
bundle = bundle_path.read_text()
if 'accessorKey:"checks_pass_pct"' not in bundle:
    errors_column = '{accessorKey:"n_errored_trials",header:({column:t})=>e.jsx("div",{className:"text-right",children:e.jsx(h,{column:t,children:"Errors"})}),cell:({row:t})=>{const s=t.original.n_errored_trials;return e.jsx("div",{className:"text-right",children:s})}}'
    old_column = '''{accessorKey:"n_failed_checks",header:({column:t})=>e.jsx("div",{className:"text-right",title:"Total failed verifier checks across this job's trials. + means some trials have no readable check report.",children:e.jsx(h,{column:t,children:"Failed checks"})}),cell:({row:t})=>{const{n_failed_checks:s,n_check_reports:n,n_total_trials:r}=t.original;return e.jsx("div",{className:"text-right tabular-nums",title:s==null?"No readable verifier check reports":`${s} failed checks across ${n} of ${r} trial reports${n<r?"; count is partial":""}`,children:s==null?"—":`${s}${n<r?"+":""}`})}}'''
    new_column = '''{accessorKey:"checks_pass_pct",header:({column:t})=>e.jsx("div",{className:"text-right",title:"Percentage of individual verifier checks passed, including skipped checks in the total. * means some trials have no readable report.",children:e.jsx(h,{column:t,children:"Checks passed (%)"})}),cell:({row:t})=>{const{checks_pass_pct:s,n_passed_checks:p,n_total_checks:c,n_failed_checks:f,n_check_reports:n,n_total_trials:r}=t.original;return e.jsx("div",{className:"text-right tabular-nums",title:s==null?"No readable verifier check reports":`${p}/${c} checks passed; ${f} failed. Reports: ${n}/${r} trials${n<r?"; percentage excludes missing reports":""}`,children:s==null?"—":`${Number(s.toFixed(1))}%${n<r?"*":""}`})}}'''
    new_option = '{value:"checks_pass_pct",label:"Checks passed (%)"}'
    if 'accessorKey:"n_failed_checks"' in bundle:
        bundle = replace_once(bundle, old_column, new_column, "existing failed checks column")
        bundle = replace_once(bundle, '{value:"n_failed_checks",label:"Failed checks"}',
                              new_option, "existing column chooser option")
    else:
        bundle = replace_once(bundle, errors_column, errors_column + "," + new_column,
                              "Errors column")
        anchor = '{value:"n_errored_trials",label:"Errors"}'
        bundle = replace_once(bundle, anchor, anchor + "," + new_option, "column chooser")
    pending.append((bundle_path, bundle))

# Validate every patch target before writing any installed files.
for path, content in pending:
    path.write_text(content)
print(f"[patch-harbor-viewer] Checks passed (%) installed ({len(pending)} files updated)")
