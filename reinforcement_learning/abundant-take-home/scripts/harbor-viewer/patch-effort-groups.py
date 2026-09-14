"""Give each task/model/effort its own viewer row and linked, filtered page."""
import ast
from pathlib import Path
import sys


def once(text, old, new):
    if text.count(old) != 1:
        raise SystemExit(f'Expected one effort patch target: {old[:110]!r}')
    return text.replace(old, new, 1)


viewer = Path(sys.argv[1]); pending = []
models_path = viewer/'models.py'; models = models_path.read_text()
if '# HARBOR_EFFORT_GROUPS' not in models:
    start = models.index('class TaskSummary('); end = models.index('class ModelPricing(')
    section = models[start:end]
    section = section.replace('    model_name: str | None = None\n', '    model_name: str | None = None\n    reasoning_effort: str | None = None\n')
    section = once(section, '    n_trials: int = 0\n', '    n_trials: int = 0\n    n_planned_trials: int | None = None\n')
    models = models[:start] + '# HARBOR_EFFORT_GROUPS\n' + section + models[end:]
    ast.parse(models); pending.append((models_path, models))

server_path = viewer/'server.py'; server = server_path.read_text()
if '# HARBOR_EFFORT_GROUPS' not in server:
    anchor = 'from harbor.viewer.models import ('
    server = once(server, anchor, '# HARBOR_EFFORT_GROUPS\nfrom harbor.viewer.effort_groups import configured_effort, with_planned_groups\n' + anchor)
    start = server.index('    def _get_all_task_summaries(')
    end = server.index('    @app.get(', start)
    section = server[start:end]
    section = once(section, '        if not trial_names:\n            return []\n', '        if not trial_names:\n            return with_planned_groups(scanner, job_name, [])\n')
    section = once(section, 'tuple[str | None, str | None, str | None, str | None, str]', 'tuple[str | None, str | None, str | None, str | None, str, str | None]')
    section = once(section, '                    task_name,\n                )', '                    task_name,\n                    configured_effort(config),\n                )')
    section = once(section, '                task_name,\n            )', '                task_name,\n                configured_effort(result.config),\n            )')
    section = once(section, '            task_name,\n        ), stats', '            task_name,\n            reasoning_effort,\n        ), stats')
    section = once(section, '                    model_name=model_name,\n', '                    model_name=model_name,\n                    reasoning_effort=reasoning_effort,\n')
    section = once(section, '        return summaries\n', '        return with_planned_groups(scanner, job_name, summaries)\n')
    server = server[:start] + section + server[end:]
    start = server.index('    def list_trials('); end = server.index('    @app.get(', start)
    section = server[start:end]
    section = once(section, '        page: int = Query(', '        effort: str | None = Query(default=None, description="Filter by configured reasoning effort; _ means unspecified"),\n        page: int = Query(')
    section = once(section, '                summary = trial_summary_from_config(name, config)\n', '                summary = trial_summary_from_config(name, config)\n                summary.reasoning_effort = configured_effort(config)\n                if effort is not None and (summary.reasoning_effort or "_") != effort:\n                    continue\n')
    section = once(section, '            # Apply filters\n', '            # Apply filters\n            if effort is not None and (configured_effort(result.config) or "_") != effort:\n                continue\n')
    section = once(section, '                    model_name=result_model_name,\n', '                    model_name=result_model_name,\n                    reasoning_effort=configured_effort(result.config),\n')
    server = server[:start] + section + server[end:]
    ast.parse(server); pending.append((server_path, server))

assets = viewer/'static/assets'
job_path = next(assets.glob('job-*.js')); job = job_path.read_text()
if 'accessorKey:"reasoning_effort"' not in job:
    job = once(job, '${encodeURIComponent(a.task_name)}`}', '${encodeURIComponent(a.task_name)}?effort=${encodeURIComponent(a.reasoning_effort||"_")}`}')
    anchor = '{accessorKey:"model_name",header:({column:a})=>e.jsx(c,{column:a,children:"Model"}),cell:({row:a})=>a.original.model_name||"-"}'
    column = '{accessorKey:"reasoning_effort",header:"Effort",cell:({row:a})=>a.original.reasoning_effort||"Unspecified"}'
    job = once(job, anchor, anchor + ',' + column)
    job = once(job, '{value:"model_name",label:"Model"}', '{value:"model_name",label:"Model"},{value:"reasoning_effort",label:"Effort"}')
    job = once(job, 'const{n_trials:s,n_completed:r}=a.original;', 'const{n_trials:n,n_completed:r,n_planned_trials:p}=a.original,s=Math.max(n,p??0);')
    pending.append((job_path, job))

task_path = next(assets.glob('task-*.js')); task = task_path.read_text()
if 'd.get("effort")' not in task:
    task = once(task, 'queryKey:["trials",s,n,N,$,m,u,x]', 'queryKey:["trials",s,n,N,$,m,u,x,d.get("effort")]')
    task = once(task, '{taskName:n,source:m,agentName:u,modelName:x}', '{taskName:n,source:m,agentName:u,modelName:x,effort:d.get("effort")}')
    old = '${encodeURIComponent(n)}/trials/${encodeURIComponent(c.name)}`'
    assert task.count(old) == 2
    task = task.replace(old, '${encodeURIComponent(n)}/trials/${encodeURIComponent(c.name)}${d.has("effort")?"?effort="+encodeURIComponent(d.get("effort")):""}`')
    pending.append((task_path, task))
if '--agent-kwarg reasoning_effort=' not in task:
    task = once(task, 'a&&r.push(`-m ${a}`),r.join(" ")',
                'a&&r.push(`-m ${a}`),(()=>{const effort=new URLSearchParams(location.search).get("effort");effort&&effort!=="_"&&r.push(`--agent-kwarg reasoning_effort=${effort}`)})(),r.join(" ")')
    pending.append((task_path, task))

trial_path = next(assets.glob('trial-*.js')); trial = trial_path.read_text()
if '"job-trials",s,l,r,a,o,i' not in trial:
    effort_query = '(new URLSearchParams(location.search).has("effort")?"?effort="+encodeURIComponent(new URLSearchParams(location.search).get("effort")):"")'
    assert trial.count('_e(s,m)') == 2
    trial = trial.replace('_e(s,m)', '_e(s,m)+' + effort_query)
    trial = once(trial, 'queryKey:["job-trials",s]', 'queryKey:["job-trials",s,l,r,a,o,i,new URLSearchParams(location.search).get("effort")]')
    filters = '{taskName:l,source:r==="_"?void 0:r,agentName:a==="_"?void 0:a,modelName:i==="_"?void 0:o==="_"?i:`${o}/${i}`,effort:new URLSearchParams(location.search).get("effort")}'
    trial = once(trial, '$e(s,1,100)', '$e(s,1,100,' + filters + ')')
    trial = once(trial, '$e(s,dt+2,100)', '$e(s,dt+2,100,' + filters + ')')
    old = 'const S=h!=="trajectory"?`?tab=${encodeURIComponent(h)}`:"";u(`${Nn(s,y)}${S}`,{replace:!0})'
    new = 'const S=new URLSearchParams;h!=="trajectory"&&S.set("tab",h);const E=new URLSearchParams(location.search).get("effort");E!==null&&S.set("effort",E);u(`${Nn(s,y)}${S.size?"?"+S.toString():""}`,{replace:!0})'
    trial = once(trial, old, new)
    pending.append((trial_path, trial))

api_path = next(assets.glob('api-*.js')); api = api_path.read_text()
if 'r?.effort!=null&&o.set("effort",r.effort)' not in api:
    start = api.index('async function Fr('); end = api.index('async function ', start + 1)
    section = api[start:end]
    section = once(section, 'r?.modelName&&o.set("model_name",r.modelName);', 'r?.modelName&&o.set("model_name",r.modelName),r?.effort!=null&&o.set("effort",r.effort);')
    api = api[:start] + section + api[end:]; pending.append((api_path, api))

for path, content in pending:
    path.write_text(content)
print(f'[patch-harbor-viewer] Effort pages installed ({len(pending)} files updated)')
