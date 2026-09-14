import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {FileBlob, Presentation, PresentationFile} from '@oai/artifact-tool';
import {resolvePresentationFont, applyPresentationChartFont, finalizePresentation} from '/Users/sheil/.codex/plugins/cache/openai-primary-runtime/presentations/26.904.11930/skills/presentations/container_tools/artifact_tool_utils.mjs';

const sourceDir = path.dirname(fileURLToPath(import.meta.url));
const build = path.join(sourceDir, 'all-tasks-99-revised');
await fs.mkdir(build, {recursive:true});
const base = path.dirname(sourceDir);
const dataOption = process.argv.find(arg => arg.startsWith('--data='));
const dataPath = dataOption ? path.resolve(dataOption.slice('--data='.length)) : path.join(base, 'data/results.json');
const data = JSON.parse(await fs.readFile(dataPath, 'utf8'));
const dataRelativePath = path.relative(base, dataPath);
const reportSupportPath = path.join(base, 'report-support-all-completed-tasks.md');
const reportScopePath = path.join(base, 'evidence/completed-task-report-scope-001.json');
const reportSupport = await fs.readFile(reportSupportPath, 'utf8');
const reportScope = JSON.parse(await fs.readFile(reportScopePath, 'utf8'));
const reportProfilesPath=path.join(base,'evidence/completed-task-profiles-001.json');
const reportProfiles=JSON.parse(await fs.readFile(reportProfilesPath,'utf8'));
const profilesByTask=Object.fromEntries(reportProfiles.profiles.map(p=>[p.task_id,p]));
if(reportScope.task_count!==11 || reportScope.counted_cells!==99 || !data.coverage.ready)throw new Error('Expected the reviewed 11-task/99-cell report scope');
const p = Presentation.create({slideSize: {width:1280,height:720}});
const font = resolvePresentationFont();
const c = {ink:'#142F3A', muted:'#536C75', teal:'#007B78', light:'#E9F3F1', amber:'#AD6520', white:'#FFFFFF', line:'#CAD9DC'};
const aliases = {
 'cpp-foxglove-sdk-parameter-handler':'Foxglove parameter handler',
 'cpp-rosbag2-mixed-serialization-playback':'rosbag2 mixed playback',
 'cpp-zenoh-cpp-connectivity-api':'Zenoh C++ connectivity',
 'diskcache-online-reshard-v2':'Diskcache resharding v2',
 'luigi-generation-target':'Luigi generation target',
 'py-rosbags-rosbag2-storage-writers':'Rosbags storage writers',
 'py-zarr-python-cast-value-scale-offset':'Zarr value codecs',
 'rs-burn-onnx-rnn-runtime-weights':'Burn ONNX runtime weights',
 'rs-burn-store-pytorch-reader-v4':'Burn checkpoint reader v4',
 'rs-rerun-chunk-optimizer':'Rerun chunk optimizer',
 'rs-zenoh-timestamp-instrumentation-v3':'Zenoh timestamps v3',
};
const selected = ['rs-rerun-chunk-optimizer','rs-zenoh-timestamp-instrumentation-v3','rs-burn-store-pytorch-reader-v4'];
const first = data.trials.filter(r=>r.first_counted_result);
const shortlistFirst = first.filter(r=>selected.includes(r.task));
const quotaAffectedCoverage = first.some(r=>r.trial.endsWith('__uuqa2f6'));
const serviceCaveat = quotaAffectedCoverage ? 'Provider quota interrupted Diskcache Fable/max uuqa2f6 after 24 assistant turns. Gateway failures affected Luigi Fable/max 6ooqjT5 before its 7200-second timeout. The existing policy counts both outcomes as failures. We cannot infer performance under healthy service from these trials. We preserve the raw outcomes and first-result selection. Recorded costs exclude unreported charges. See the provider-quota and Luigi timeout evidence reviews.' : '';
function shortlistModelScore(models) {
 const rows=shortlistFirst.filter(r=>models.includes(r.model));
 return `${rows.filter(r=>r.reward===1).length}/${rows.length}`;
}
const date = data.generated_at.slice(0,16).replace('T',' ')+' UTC';
const foot = `Snapshot ${date}. ${data.coverage.covered}/99 combinations. First counted result per combination`;
let number = 0;
function text(s, value, x, y, w, h, size=28, color=c.ink, bold=false) {
 const q=s.shapes.add({geometry:'textbox',position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});
 q.text=String(value);q.text.style={typeface:font,fontSize:size,color,bold,autoFit:'none'};return q;
}
function line(s,x,y,w,color=c.line) {s.shapes.add({geometry:'rect',position:{left:x,top:y,width:w,height:2},fill:color,line:{fill:'none',width:0}});}
const noteEdits={
  "This report covers all 11 tasks with a completed first-result sweep: one counted outcome in each of three models by three effort levels, for nine cells per task. A completed sweep here does not mean 20 repeated trials per cell.": "We report one counted outcome for each of 11 tasks, three models and three effort levels: nine settings per task. The sweep contains one first result per setting. It does not contain 20 repeats per setting.",
  "We use this as pilot evidence. Reliable per-setting pass rates and causal claims about model or effort require repeated trials.": "We recommend a pilot from this evidence. We need repeated trials to estimate pass rates or test effects of model and effort.",
  "Raw pass counts do not by themselves establish learning headroom.": "We need failure audits and repeated trials to assess learning headroom.",
  "In these three original first-result cohorts, Fable and Opus pass all 18 settings combined; Sonnet passes five of nine.": "In the three sample cohorts, Fable and Opus pass all 18 settings combined. Sonnet passes five of nine.",
  "This supports a pilot focused on Sonnet, not demonstrated headroom for every evaluated model.": "We recommend a pilot focused on Sonnet. We observed no Fable or Opus failures in these sample cohorts.",
  "Exact controls and failure artifacts are described in evidence/shortlist-cases.json.": "See evidence/shortlist-cases.json for the exact controls and failure artifacts.",
  "An agent step is one assistant turn in the saved ATIF trajectory. Tool calls and elapsed time are separate measurements. Median is across successful first counted results for each task. Failures, including the service-interrupted Luigi and Diskcache outcomes, are excluded from this chart. These descriptive turn counts do not establish a causal effect of effort or a human completion-time estimate.": "We count one assistant turn in the saved ATIF trajectory as one step. We record tool calls and elapsed time as separate measurements. The chart gives the median among successful first results for each task. We omit failures, including the Luigi and Diskcache service interruptions. We cannot infer a causal effort effect or human completion time from these turn counts.",
  "This is not evidence of data loss. Existing regression suites pass; five feature/contract tests fail. Quote: native assistant turn138 / ATIF step140.": "We did not establish data loss. The submission passes existing regressions and fails five feature/contract tests. Quotation source: native assistant turn 138 / ATIF step 140.",
  "This is consistent with the packing and splitting failures; no replay proves it is the sole cause.": "The size discrepancy could account for the packing and splitting failures. We did not run a replay to establish the sole cause.",
  "Audited Sonnet/medium YtCswNv uses ZenohIdProto in TimestampContext.zid; instruction explicitly requires ZenohId.": "Sonnet/medium YtCswNv uses ZenohIdProto in TimestampContext.zid. The instruction requires ZenohId.",
  "The completed quality002 review passes all 11 criteria in the officially collected report; this is an automated review judgment, not proof of exhaustive boundary coverage.": "The collected quality002 report records passes for all 11 criteria. This automated judgment does not establish exhaustive boundary coverage.",
  "All nine saved-source regrades on the normal verifier image are complete: eight pass all 11 verifier groups, and YtCswNv retains reward 0 because of the same explicit public-type mismatch.": "We completed nine saved-source regrades on the normal verifier image. Eight submissions pass all 11 verifier groups. YtCswNv retains reward 0 because of the same required public-type mismatch.",
  "That failing invocation records a build failure and skips the remaining nine groups. It does not execute all runtime behaviors.": "The failing invocation stops at compilation and skips the remaining nine groups. We therefore have no runtime result for those groups.",
  "Exact source, tests, normal image, limits, deadline and cleanup checks passed.": "We verified the source and test hashes, normal image, resource limits, deadline and cleanup.",
  "They do not establish exhaustive Send/Route cap coverage.": "We have not established exhaustive Send/Route cap coverage.",
  "This is not a corrected-v4 grade.": "The repeat used v3, not the corrected v4 verifier.",
  "Raw reward remains 0; this outcome is not a functional failure or evidence of cheating.": "We retain raw reward 0 for the source restriction. The functional checks passed. We found no evidence of cheating.",
  "Exact allocation origin was not traced.": "We did not trace the allocation origin.",
  "30 counted attempts span nine cells in two campaigns; all fail NUL, only three exclusively.": "We reviewed 30 counted attempts across nine cells in two campaigns. All 30 fail NUL. Three fail no other tests.",
  "Huey/SQLite held revisions are excluded from the 99-cell gate. Burn Debug and Zenoh import fixes had separate validated revisions.": "We exclude the held Huey/SQLite revisions from the 99-cell grid. We validated the Burn Debug and Zenoh import corrections as separate revisions.",
  "SWE-Marathon paper, June5 2026, abstract:": "SWE-Marathon paper, June 5, 2026, abstract:",
  "It reports20tasks and fewer than30% solved by the evaluated frontier coding agents; this is a paper-era result, not a current leaderboard or a result for our three model aliases.": "The authors evaluated frontier coding agents on 20 tasks and reported fewer than 30% solved. These results describe the agents evaluated in the paper. They do not describe a current leaderboard or our three model aliases.",
  "Short verbatim quote below is from the abstract.": "The quoted words come from the abstract.",
  "Our inference: a narrower task family can test whether agents verify the complete feature contract.": "We use the paper to motivate a test of how agents verify complete feature requirements. This connection is our inference.",
  "This pilot has no human time baseline, so assistant-turn counts cannot be converted into that metric.": "We did not measure a human time baseline, so we cannot convert assistant turns into that metric.",
  "Public upstream provenance and recent commits do not prove that a model has never seen a task or its implementation.": "We cannot establish task novelty to a model from public upstream provenance or recent commit dates.",
  "Proposed scaling design, not a measured production yield or budget.": "We propose this scaling design. We have not measured its production yield or budget.",
  "A 20% acceptance scenario needs 5,000 mined candidates for 1,000 accepted tasks; actual yield must be measured in a staged pilot.": "At 20% acceptance, 5,000 mined candidates would yield 1,000 accepted tasks. We need staged pilot batches to measure the actual yield.",
  "All 11 completed tasks remain in the reported population.": "We report all 11 completed task cohorts.",
  "The report covers all 11 completed task cohorts.": "We report all 11 completed task cohorts.",
  "Every cell is": "Each cell is",
  "no observed failure headroom": "no observed first-result failures",
  "This is the strongest already-built depth alternative by this descriptive median.": "This built alternative has a median of 225 turns among successful first results.",
  "All nine first cells pass, and every success exceeds75 assistant turns (median163, range79\\u2013364).": "All nine first cells pass. Each success exceeds 75 assistant turns (median 163, range 79–364).",
  "This supports sustained implementation depth across the tested settings; the first grid provides no observed failure headroom.": "Agents used more than 75 turns in all nine successful settings. None of the first-result runs failed."
};
function reviseNotes(value) {
 let out=String(value);
 for(const [before,after] of Object.entries(noteEdits))out=out.split(before).join(after);
 out=out.replace(/\\u2013/g,'–').replace(/\b(exceeds|exceed|median|range|after|with|above|pass|passes|only|all|and|leaving|at)(?=\d)/g,'$1 ').replace(/(comparisons,)(?=\d)/g,'$1 ').replace(/(turn|step)(?=\d)/g,'$1 ');
 return out;
}
function slide(title, notes='', subtitle='') {
 const s=p.slides.add();s.background.fill=c.white;number++;
 text(s,title,64,40,1152,104,40,c.ink,true);
 if(subtitle)text(s,subtitle,68,142,1144,52,23,c.muted);
 line(s,68,658,1144);text(s,foot,68,675,1100,28,14,c.muted);text(s,number,1175,675,35,28,14,c.muted);
 s.speakerNotes.textFrame.setText(reviseNotes(notes)+'\n\nData: '+dataRelativePath+' and its adjacent trials.csv. These files preserve raw artifact paths and SHA-256 hashes.\n'+foot);
 return s;
}
function para(s,title,body,x,y,w=530) {text(s,title,x,y,w,42,29,c.teal,true);text(s,body,x,y+55,w,190,26,c.ink);}
function table(s,values,x,y,w,h,widths,size=22) {
 const t=s.tables.add({rows:values.length,columns:values[0].length,left:x,top:y,width:w,height:h,columnWidths:widths,values});
 t.borders.assign({fill:c.white,width:1});
 for(let r=0;r<values.length;r++)for(let col=0;col<values[0].length;col++) {
  const cell=t.getCell(r,col);cell.fill=r===0?c.ink:r%2?c.light:c.white;
  cell.text.style={typeface:font,fontSize:size,color:r===0?c.white:c.ink,bold:r===0,autoFit:'none'};
 }
 return t;
}
function trial(suffix) {return data.trials.find(r=>r.trial.endsWith('__'+suffix));}
function resultLabel(id) {const r=trial(id);return r?`${r.model.replace('-5-1','').replace('-5','')}/${r.effort}, ${r.assistant_steps} steps, ${r.reward===1?'pass':'fail'}`:id;}

{
 const s=slide('Executive summary', 'We recommend Rerun chunk optimizer, Zenoh timestamp instrumentation and Burn checkpoint reader as Harbor samples. The report covers all 11 task cohorts in the frozen data, with three models and three effort levels per task. One counted result per setting gives 99 first results. Agents passed 76, and 64 of the 76 successful attempts exceeded 75 assistant turns. Requirement audits identify chunk-layout failures, the Zenoh public-type mismatch and Burn parser/validation failures. Burn also includes a source-restriction failure. The sample first-result cohorts have 18/18 Fable/Opus passes and 5/9 Sonnet passes. We use this as pilot evidence. Reliable per-setting pass rates and causal claims about model or effort require repeated trials. Provider interruptions affect the Diskcache and Luigi Fable/max outcomes. Corrected Zenoh verification regraded nine saved submissions, with eight passes and the same type failure, adding zero model trials. Sources: evidence/completed-task-report-scope-001.json, evidence/completed-task-profiles-001.json, evidence/shortlist-cases.json, report-support-all-completed-tasks.md and research/zenoh-coverage-followup-2026-09-14/final-revision-mapping-001.json. '+serviceCaveat);
 text(s,'Recommend Rerun, Zenoh timestamps\nand Burn checkpoint reader',68,166,1144,90,34,c.teal,true);
 text(s,'11 tasks × 3 models × 3 efforts = 99 first counted results',68,267,1144,42,27,c.ink);
 text(s,'76 passed. 64 of 76 successful attempts exceeded 75 assistant turns.',68,318,1144,60,27,c.ink);
 text(s,'On the three samples, Fable and Opus passed 18/18. Sonnet passed 5/9.',68,388,1144,42,24,c.ink);
 text(s,'Counted failures include service interruptions.',68,437,1144,36,24,c.amber);
 text(s,'In audited Rerun and Zenoh attempts, agents passed their own checks but missed required chunk layouts or public types.',68,482,1144,76,26,c.ink);
 text(s,'We recommend a pilot. We need repeated trials to estimate pass rates and compare models.',68,576,1144,70,26,c.muted);
}

{
 const s=slide('Software integration across modules', 'This report covers all 11 tasks with a completed first-result sweep: one counted outcome in each of three models by three effort levels, for nine cells per task. A completed sweep here does not mean 20 repeated trials per cell. Three Rust tasks remain detailed case studies and recommended Harbor samples. The final corrected Zenoh verifier also completed all nine saved-submission regrades: eight passes and one unchanged failure. These regrades add no model calls or counted sweep trials. '+serviceCaveat, '11 completed task cohorts across C++, Rust and Python');
 text(s,'Implementing features across APIs,\ndata formats and storage systems',68,226,1110,134,38,c.ink,true);
 const stats=[`${data.coverage.covered}/99`,`${data.first_results.successful_over_75_steps}/${data.first_results.successful_steps_available}`,'11'];
 const labels=['combinations with counted results','successful trials above 75 assistant turns','evaluated tasks, with three detailed cases'];
 stats.forEach((v,i)=>{text(s,v,68+i*390,429,360,84,62,c.teal,true);text(s,labels[i],68+i*390,530,350,64,24,c.muted);});
}
{
 const s=slide('Feature requirements across three languages', 'Scope and task-level evidence: report-support-all-completed-tasks.md and evidence/completed-task-report-scope-001.json. Three detailed task sources: candidates_v2/rs-rerun-chunk-optimizer/instruction.md; research/task-revisions/rs-zenoh-timestamp-instrumentation-v3/instruction.md; research/task-revisions/rs-burn-store-pytorch-reader-v4/instruction.md. The full population contains three C++ tasks, four Python tasks and four Rust tasks. Each has nine counted first-result cells. The three detailed Rust cases cover data representations, streaming or deferred materialization, public interfaces and independent observable behavior.');
 para(s,'C++','Extend callback, playback and connectivity APIs while preserving compatibility and observable behavior.',68,196,350);
 para(s,'Python','Implement storage writers, codecs and persistence features with explicit recovery and representation contracts.',465,196,350);
 para(s,'Rust','Integrate checkpoint readers, runtime weights, streaming layouts and timestamp metadata across modules.',863,196,350);
 line(s,68,498,1144,c.teal);text(s,'Agents must preserve the specified behavior across module boundaries.',68,533,1120,80,30,c.ink,true);
}
{
 const values=[['Task','Fable','Opus','Sonnet','Covered']];
 for(const task of Object.keys(aliases)) {
  const g=data.tasks[task];
  values.push([aliases[task],...['fable-5-1','opus-5','sonnet-5'].map(m=>`${g.by_model[m]?.passes??0}/${g.by_model[m]?.counted??0}`),`${g.first_results.counted}/9`]);
 }
 const s=slide('Passes in the first result grid', 'Frozen scope: coverage-scope.json (11 tasks × 3 models × 3 efforts). Each number is passes / first counted results, pooled across effort for description only. Outcomes classified as infrastructure do not count. Budgeted timeouts and validated abnormal exits follow the existing counting policy. Repeat trials and superseded revisions are separate. No model ranking or estimated win rate is asserted. '+serviceCaveat, quotaAffectedCoverage ? 'Passes / counted results. Service interruptions affected Luigi and Diskcache.' : 'Passes / completed first results across medium, high and max effort');
 table(s,values,68,184,1144,452,[540,145,145,145,169],19);
}
// All 99 cells remain separate from repeated trials and revised-verifier regrades.
{
 const taskKeys=Object.keys(aliases);
 const columns=['Task','Fable\nMed','Fable\nHigh','Fable\nMax','Opus\nMed','Opus\nHigh','Opus\nMax','Sonnet\nMed','Sonnet\nHigh','Sonnet\nMax'];
 const shortNames={...aliases,'cpp-foxglove-sdk-parameter-handler':'Foxglove parameters','py-rosbags-rosbag2-storage-writers':'Rosbags storage writers','rs-burn-onnx-rnn-runtime-weights':'Burn ONNX weights','rs-burn-store-pytorch-reader-v4':'Burn checkpoint reader v4'};
 const models=['fable-5-1','opus-5','sonnet-5'];
 const efforts=['medium','high','max'];
 for(const [index,keys] of [taskKeys.slice(0,6),taskKeys.slice(6)].entries()) {
  const grid=[columns];
  for(const task of keys) {
   const cells=[];
   for(const model of models)for(const effort of efforts) {
    const rows=first.filter(r=>r.task===task&&r.model===model&&r.effort===effort);
    if(rows.length!==1)throw new Error(`Expected exactly one first result for ${task}/${model}/${effort}`);
    const r=rows[0];const interrupted=r.trial.endsWith('__uuqa2f6')||r.trial.endsWith('__6ooqjT5');
    const status=r.status==='verifier_timeout'?'V':r.status==='timeout'?'A':r.reward===1?'P':'F';
    cells.push(`${status} (${r.assistant_steps})${interrupted?'*':''}`);
   }
   grid.push([shortNames[task],...cells]);
  }
  const s=slide(`Results by model and effort (${index+1} of 2)`, 'Each cell is the exact first counted result from the frozen 99 snapshot, displayed as pass/fail and assistant turns. The population is 11 tasks × three models × three effort levels. Model aliases are fable-5-1, opus-5 and sonnet-5. Medium/high/max are configured effort levels. Counted failures include policy-counted agent timeouts, a verifier timeout, and validated abnormal exits. They are not all ordinary assertion failures. Diskcache Sonnet/high is a verifier timeout; Diskcache Sonnet/max, Luigi Sonnet/max and Luigi Fable/max are agent timeouts. Historical Zenoh v3 is the agent-run cohort. Corrected saved-submission regrades add no new model trials. '+serviceCaveat, 'One counted result per task, model and effort. Repeated trials have separate records.');
  table(s,grid,68,205,1144,392,[280,...Array(9).fill(96)],20);
  text(s,'P pass / F scored failure / A agent timeout / V verifier timeout. (n) turns. * Service affected.',68,619,1144,31,21,c.muted);
 }
}

// Language-group interpretations are populated from the reviewed task profiles.
const interpretationGroups=[
  [
    "C++: API and playback integration",
    [
      [
        "cpp-foxglove-sdk-parameter-handler",
        "Foxglove parameters",
        "Expose delayed responses through Rust, C FFI and C++ while preserving ownership.",
        "All nine pass above 75 turns. Sustained work, with no observed failure in this first cohort."
      ],
      [
        "cpp-rosbag2-mixed-serialization-playback",
        "rosbag2 mixed playback",
        "Play mixed serialization formats and filter topics while preserving raw rewrite behavior.",
        "One failure violates warning rules. The other fails an ambiguous writer assertion. Treat them separately."
      ],
      [
        "cpp-zenoh-cpp-connectivity-api",
        "Zenoh C++ connectivity",
        "Wrap transport and link events across two C++ backends with ownership and callback parity.",
        "The only failure is an upstream segfault. New connectivity checks pass. Its cause remains unresolved."
      ]
    ]
  ],
  [
    "Python: persistence and format contracts",
    [
      [
        "diskcache-online-reshard-v2",
        "Diskcache resharding",
        "Reshard live caches with crash recovery and mutation reconciliation.",
        "Timeouts and quota affect the grid. Some failures resurrect deleted data."
      ],
      [
        "luigi-generation-target",
        "Luigi generation target",
        "Publish atomic file generations with stable snapshots and crash recovery.",
        "Some lifecycle rules fail. One of the two timeouts is gateway affected."
      ],
      [
        "py-rosbags-rosbag2-storage-writers",
        "Rosbags storage writers",
        "Write SQLite3 and MCAP bags with metadata, compression and interoperability.",
        "All nine pass. Only 3/9 successes exceed 75 turns. No failures in these nine attempts."
      ],
      [
        "py-zarr-python-cast-value-scale-offset",
        "Zarr value codecs",
        "Compose cast and scale/offset codecs with exact metadata and integer semantics.",
        "Cast-value parity passes. Validation, JSON and integer scale/offset failures remain."
      ]
    ]
  ],
  [
    "Rust: runtime behavior and regression failures",
    [
      [
        "rs-burn-onnx-rnn-runtime-weights",
        "Burn ONNX weights",
        "Use runtime RNN weights while preserving layouts, outputs and initializers.",
        "Eight pass above 75 turns. The failure is a snapshot regression. Runtime checks pass."
      ],
      [
        "rs-burn-store-pytorch-reader-v4",
        "Burn checkpoint reader",
        "Read checkpoint data on demand, reject unsafe input and honor source constraints.",
        "One functional failure and one source restriction failure. Sonnet max passes."
      ],
      [
        "rs-rerun-chunk-optimizer",
        "Rerun chunk optimizer",
        "Stream and repack chunks while preserving cells, memory bounds and layout.",
        "One required layout failure. Sonnet max passes after a longer trajectory."
      ],
      [
        "rs-zenoh-timestamp-instrumentation-v3",
        "Zenoh timestamps v3",
        "Carry timestamps through public APIs, wire encoding and receive paths.",
        "The same public-type failure persists. All eight other saved regrades pass."
      ]
    ]
  ]
];
for(const [title,entries] of interpretationGroups) {
 const values=[['Task / first results','Required behavior','Interpretation']];
 const detailed=[];
 for(const [task,label,behavior,interpretation] of entries) {
  const stats=data.tasks[task].first_results;
  values.push([`${label}\n${stats.passes}/9 pass, ${stats.median_successful_assistant_steps} turns`,behavior,interpretation]);
  const profile=profilesByTask[task];
  detailed.push(`${task}: ${profile ? profile.behavior+' '+profile.interpretation+' '+profile.failure_validity : behavior+' '+interpretation}`);
 }
 let detail=detailed.join('\n\n');
 detail=detail.replace(/\\u2013/g,'–').replace(/\b(exceeds|exceed|median|range|after|with|above|pass|passes|only)(?=\d)/g,'$1 ').replace(/(Sonnet\/(?:medium|high|max)|Opus\/max|Fable\/max)(?=[A-Za-z0-9])/g,'$1 ');
 const s=slide(title, 'This language-group overview covers every task in the frozen 99 population, not only the three recommended sample tasks. Each first-result cohort has exactly nine model/effort cells. Median turns are among successful first results. Interpretations distinguish observed test failures from independently established causes. Sources: report-support-all-completed-tasks.md, evidence/completed-task-report-scope-001.json, evidence/completed-task-profiles-001.json and the task instructions and raw artifacts cited there. '+detail+'\n\n'+serviceCaveat, 'Nine first results per task. Turns are the median among successful trials.');
 table(s,values,68,188,1144,422,[284,382,478],21);
}


{
 const ranked=Object.entries(data.tasks).filter(([,g])=>g.first_results.successful_steps_available>0).sort((a,b)=>b[1].first_results.median_successful_assistant_steps-a[1].first_results.median_successful_assistant_steps);
 const s=slide('64 of 76 successes exceeded 75 assistant turns','An agent step is one assistant turn in the saved ATIF trajectory. Tool calls and elapsed time are separate measurements. Median is across successful first counted results for each task. Failures, including the service-interrupted Luigi and Diskcache outcomes, are excluded from this chart. These descriptive turn counts do not establish a causal effect of effort or a human completion-time estimate.', `Median turns on success. ${data.first_results.successful_over_75_steps}/${data.first_results.successful_steps_available} successful trials exceed 75 turns`);
 const ch=s.charts.add('bar',{position:{left:68,top:205,width:1144,height:425},categories:ranked.map(([k])=>aliases[k]),series:[{name:'Median assistant turns',values:ranked.map(([,g])=>g.first_results.median_successful_assistant_steps),fill:c.teal}],barOptions:{direction:'bar',grouping:'clustered',gapWidth:50},hasLegend:false,xAxis:{visible:false,majorGridlines:null,textStyle:{fontSize:18,fill:c.ink}},yAxis:{textStyle:{fontSize:18,fill:c.ink},line:{fill:c.line,width:1}},dataLabels:{showValue:true,position:'outEnd',textStyle:{fontSize:18,fill:c.ink}},chartFill:c.white,plotAreaFill:c.white});
 applyPresentationChartFont(ch,{fontFamily:font});
}
{
 const s=slide('Recommended Harbor samples','All 11 completed tasks remain in the reported population. These three Rust tasks are the detailed case studies and recommended Harbor samples, based on task coherence, reproducibility, observed work and audited contract failures. Raw pass counts do not by themselves establish learning headroom. In these three original first-result cohorts, Fable and Opus pass all 18 settings combined; Sonnet passes five of nine. This supports a pilot focused on Sonnet, not demonstrated headroom for every evaluated model. Zenoh v3 outcomes remain historical. All nine corrected-grader replays are complete, with eight passes and the same explicit public-type failure. These saved-submission regrades are separate and add no model trials. The packaged Zenoh sample uses the final corrected validation revision. Its exact mapping is research/zenoh-coverage-followup-2026-09-14/final-revision-mapping-001.json. All three have successful model completions. Steps on success are median assistant turns among successful first results. Exact controls and failure artifacts are described in evidence/shortlist-cases.json.','Rerun, Zenoh timestamps and Burn checkpoint reader');
 const vals=[['Candidate','Pass / first trials','Median turns on success','Reason to retain']];
 const reasons=['Chunk layout and data preservation','Wire protocol and public API integration','Format compatibility and bounds checks'];
 selected.forEach((task,i)=>{const g=data.tasks[task].first_results;vals.push([aliases[task],`${g.passes}/${g.counted}`,`${g.median_successful_assistant_steps}`,reasons[i]]);});
 table(s,vals,68,194,1144,288,[310,185,225,424],24);
 text(s,'Sonnet failed four of the nine sample settings',68,516,1100,40,27,c.amber,true);
 text(s,`Fable and Opus: ${shortlistModelScore(['fable-5-1','opus-5'])} passes. Sonnet: ${shortlistModelScore(['sonnet-5'])} passes.\nWe retain separate records for repeats and corrected Zenoh regrades.`,68,565,1110,75,26,c.ink);
}
{
 const s=slide('Rerun: incorrect chunk layout','Audited trial EeEppp5: Sonnet/medium, 138 assistant steps, reward 0. The diagram shows actual rows-per-output-chunk [6,2] versus expected [4,4] in chunks_that_do_not_fit_together_emit_alone_in_order (optimizer_contract.rs:410). This is not evidence of data loss. Existing regression suites pass; five feature/contract tests fail. Quote: native assistant turn138 / ATIF step140. Compare Sonnet/max iUZ6GKK (383 steps, reward 1). Full hashes and instruction/test links: evidence/shortlist-cases.json. Separate repeat YSSc6xQ (Sonnet/medium) takes 124 turns and fails nine of 96 tests. It measures Arc<Chunk>, whose attributed size varies with reference count, instead of the specified decoded Chunk. This is consistent with the packing and splitting failures; no replay proves it is the sole cause. Opus/max YSPYcnB passes in 132 turns with 148 tool calls. See evidence/repeat-case-audit-20260914T1018.json and evidence/repeat-case-root-review-20260914T1033.json. These repeats do not replace the first-result cohort.');
 function chunks(label,values,y,color) {
  text(s,label,68,y,510,34,25,c.ink,true);let x=68;
  for(const value of values) {
   const width=value*55;
   s.shapes.add({geometry:'rect',position:{left:x,top:y+44,width,height:57},fill:color,line:{fill:'none',width:0}});
   text(s,String(value),x+12,y+54,width-18,40,25,c.white,true);x+=width+12;
  }
 }
 chunks('Returned layout, rows per chunk',[6,2],191,c.amber);
 chunks('Required layout, rows per chunk',[4,4],325,c.teal);
 text(s,'“All existing tests for the three crates pass, plus my own smoke tests”',660,204,548,109,29,c.ink,true);
 text(s,'Sonnet failed five split, packing and row-limit checks while passing the existing regression suites.',660,345,548,112,26,c.ink);
table(s,[['Sonnet attempts on the same task','Observed result'],[resultLabel('EeEppp5'),'Layout contract failures'],[resultLabel('iUZ6GKK'),'All verifier groups passed']],68,479,1144,140,[590,554],23);
}
{
 const s=slide('Zenoh: Sonnet used the wrong public type','Audited Sonnet/medium YtCswNv uses ZenohIdProto in TimestampContext.zid; instruction explicitly requires ZenohId. This is distinct from the old hidden-import test mismatch corrected in revision v3. Sonnet/max gSYDMEG passes the historical v3 verifier in 626 steps. The separate revised validation task adds direct malformed/boundary-stack and admin-space Receive tests, plus a reference fix preserving the received timestamp stack in replies. Its confirmed controls are oracle 1 and no-op 0. The completed quality002 review passes all 11 criteria in the officially collected report; this is an automated review judgment, not proof of exhaustive boundary coverage. All eight focused diagnostics are complete: six targeted omissions fail their intended assertions, and both exact saved submissions pass all seven new tests. These checks used the cached build image. All nine saved-source regrades on the normal verifier image are complete: eight pass all 11 verifier groups, and YtCswNv retains reward 0 because of the same explicit public-type mismatch. That failing invocation records a build failure and skips the remaining nine groups. It does not execute all runtime behaviors. The regrades make zero model calls and add zero counted sweep trials. Exact source, tests, normal image, limits, deadline and cleanup checks passed. Controls remain oracle 1 and no-op 0. The final task checksum is 67f1fdca75fd66f4cbdd6e4729a582c9fbbdac428a0299eefc74104dffa59fab. See research/zenoh-coverage-followup-2026-09-14/paired-regrades/run-002/summary.json, final-revision-mapping-001.json (SHA-256 5b6696bec407839159522168904ec22ad393a75058d79522d86a7b4565f45608) and final-revision-mapping-review-001/review.json. The new boundary tests cover Receive/admin-space and malformed protocol cases. They do not establish exhaustive Send/Route cap coverage. See evidence/zenoh-eight-focused-root-review-20260914T0948.json. Original v3 scores remain unchanged. See evidence/zenoh-quality-final-root-review-20260914T0841.json, evidence/zenoh-continuation004-root-review-20260914T0852.json, and research/zenoh-coverage-followup-2026-09-14/reviews-final-002/summary.json. A separate Sonnet/max repeat, 6nTASYR, passes the historical v3 verifier in 621 turns. This is not a corrected-v4 grade. See evidence/repeat-case-audit-20260914T1018.json and evidence/repeat-case-root-review-20260914T1033.json.','Corrected verifier: eight passes and the same API failure in nine saved submissions');
 para(s,'Required public type','The instruction requires ZenohId for TimestampContext.zid. Sonnet used the wire type ZenohIdProto.',68,198,540);
 para(s,'Downstream compilation','Downstream users compile against the specified API. The type mismatch prevents that code from compiling.',677,198,535);
 text(s,'“Everything is green with both feature configurations.”',68,402,1120,42,24,c.muted);
table(s,[['Sonnet effort','Observed result'],['Medium, 377 steps','Type mismatch fails both verifiers'],['Max, 626 steps','Passes both v3 and corrected verifiers']],68,479,1144,140,[590,554],23);
}
{
 const s=slide('Burn: parser failures and a source restriction','Revision v4. Sonnet/medium vXNtQ5E failed FRAME/TAR/storage-bounds functionality in 42 steps. Sonnet/high BrRKozk completed in 251 steps and passed the functional groups, but retained a prohibited fixture path in baseline-derived inline test code. Raw reward remains 0; this outcome is not a functional failure or evidence of cheating. Sonnet/max X6pyirF passed all eight verifier groups in 323 steps, including 62 matrix cases and 32 rejection cases. Fable/max and Opus/max both pass in 158 steps. See evidence/burn-v4-sonnet-max.json and evidence/shortlist-cases.json. Separate repeat UDNoWje takes 191 turns and passes 217 Rust tests and the 62-case fixture matrix, but an allocation abort on a 125-byte doubling-bomb fixture violates the explicit R12 requirement to return an error. Exact allocation origin was not traced. Sonnet/max kYchPug and Fable/max RBjsods later pass all eight groups in 274 and 171 turns. These repeats remain separate from the first-result cohort. See evidence/repeat-case-audit-20260914T0923.json, evidence/repeat-case-root-review-20260914T0929.json, evidence/repeat-case-audit-20260914T1018.json and evidence/repeat-case-root-review-20260914T1033.json.');
 table(s,[['Observation','Interpretation'],['Sonnet medium, 42 steps','Parser and validation failures'],['Sonnet high, 251 steps','Functional pass, source restriction failure'],['Sonnet max, 323 steps','Passes all verifier groups'],['Fable / Opus max, 158 steps each','Both pass all verifier groups']],68,194,1144,350,[460,684],25);
 text(s,'Seven successful attempts took 76–323 turns. Failure causes include parser behavior and a source restriction.',68,577,1120,66,26,c.ink,true);
}
{
 const s=slide('Failure review and test corrections','SQLite NUL audit: research/sqlite-nul-validity-audit-2026-09-14/{README.md,results.json,sqlite-default-proof.json}. 30 counted attempts span nine cells in two campaigns; all fail NUL, only three exclusively. NUL is valid under the all-strings contract and SQLite supports embedded NUL: https://www.sqlite.org/nulinstr.html . Huey/SQLite held revisions are excluded from the 99-cell gate. Burn Debug and Zenoh import fixes had separate validated revisions.','The held SQLite task is outside the 11-task result grid');
 para(s,'NUL defaults in SQLite','The task’s all-strings requirement includes NUL defaults. All 30 attempts failed that test. Three failed no other checks.',68,198,540);
 para(s,'Corrections to hidden tests','Reviewers corrected hidden internal imports, an unstated Debug bound and an empty-rename rejection. We record revised tasks and saved-submission regrades separately.',677,198,535);
 text(s,'Keep separate counts for implementation defects, source restrictions, test/spec mismatches and infrastructure failures.',68,550,1120,89,28,c.teal,true);
}
{
 const s=slide('Self-verification failures in SWE-Marathon','SWE-Marathon paper, June5 2026, abstract: https://arxiv.org/abs/2606.07682v1 . It reports20tasks and fewer than30% solved by the evaluated frontier coding agents; this is a paper-era result, not a current leaderboard or a result for our three model aliases. Short verbatim quote below is from the abstract. These tasks, budgets and metrics differ from our pilot. Our inference: a narrower task family can test whether agents verify the complete feature contract.');
 text(s,'20',68,206,320,95,70,c.teal,true);text(s,'tasks in SWE-Marathon',68,315,490,52,28,c.ink);
 text(s,'<30%',677,206,400,95,70,c.teal,true);text(s,'solved by agents evaluated in the paper',677,315,535,78,28,c.ink);
 text(s,'“poor self-verification, self-reported infeasibility, and premature termination”',68,438,1120,94,31,c.ink,true);
 text(s,'We compare submissions with the feature requirements and the results of local checks.',68,555,1120,80,28,c.muted);
}
{
 const s=slide('Pilot findings and measurement limits','Sources: https://arxiv.org/abs/2509.16941v2 (SWE-Bench Pro, abstract) and https://arxiv.org/abs/2503.14499v4 (METR, abstract). SWE-Bench Pro characterizes complex software tasks involving hours to days of human work. METR defines its horizon using measured human time and a success threshold. This pilot has no human time baseline, so assistant-turn counts cannot be converted into that metric. Public upstream provenance and recent commits do not prove that a model has never seen a task or its implementation. '+serviceCaveat);
 para(s,'Observed task completions','Agents passed 76 of 99 first counted results. In 64 successful attempts, they used more than 75 turns. Reviewers traced several failures to stated requirements.',68,196,540);
 para(s,'Measurements for another study','We need repeated trials to estimate pass rates. We cannot rank models or attribute changes to effort. We have not measured human time or training gains.',677,196,535);
 if(quotaAffectedCoverage)text(s,'Service interruptions affect the Fable/max outcomes in Luigi and Diskcache.',68,437,1120,60,25,c.amber,true);
 text(s,'SWE-Bench Pro: “patches across multiple files and substantial code modifications.”',68,509,1120,50,25,c.muted);
 text(s,'METR uses measured human completion time. We count assistant turns without a human baseline.',68,576,1120,60,25,c.muted);
}
{
 const s=slide('Scaling to 1,000 accepted tasks','Proposed scaling design, not a measured production yield or budget. Source candidates from public C++, Rust and Python data, storage, serialization and protocol repositories. Deduplicate related PR stacks and templates before splitting. Hold out repositories or families to test transfer. A 20% acceptance scenario needs 5,000 mined candidates for 1,000 accepted tasks; actual yield must be measured in a staged pilot.');
 const labels=['Mine issues and PR stacks','Compose dependent changes','Audit requirements and controls','Hold out task families'];
 labels.forEach((v,i)=>{text(s,String(i+1).padStart(2,'0'),68+i*296,211,270,56,42,c.teal,true);text(s,v,68+i*296,284,260,100,28,c.ink,true);});
 text(s,'Public sources: Rerun, Burn, Zenoh, Foxglove, Luigi and Zarr',68,397,1120,46,25,c.muted);
text(s,'Pilot batches: 25, then 100, then 1,000 accepted tasks',68,451,1120,64,34,c.ink,true);
 text(s,'Track acceptance yield, authoring time, task validity, runtime cost and distinct failure mechanisms at each stage.',68,535,1120,98,28,c.ink);
}
{
 const s=slide('Task pack and next experiment','The report covers all 11 completed task cohorts. The three recommended Rust samples have exact-package reference/no-op controls and required task checks. The final Zenoh sample uses the corrected validation revision. All nine saved-submission regrades retain their original outcomes, with eight passes and one explicit public-type failure. The original v3 first-result cohort stays separate. See research/zenoh-coverage-followup-2026-09-14/final-revision-mapping-001.json. Preserve raw Harbor job/trial structure and bytes. Keep discarded candidates in archive. Repeat trials within fixed task/model/effort cells to estimate pass rates, separating infrastructure outcomes and task revisions. '+serviceCaveat);
 para(s,'Evidence for the report','We retain results for all 11 tasks, raw artifact hashes and turn counts. The three sample cases include failed attempts and successful contrasts.',68,198,540);
 para(s,'Next experiment',quotaAffectedCoverage?'After provider access returns, repeat fixed task/model/effort settings. Estimate pass rates with uncertainty and review recurring failures against the requirements.':'Repeat each fixed task/model/effort combination. Estimate pass rates with uncertainty, then compare recurring failures against the task contract.',677,198,535);
 text(s,quotaAffectedCoverage?'We have 99 counted results. Provider usage limits interrupted the sweep.':data.coverage.ready?'First-result coverage is complete at 99/99. Repeated trials remain separate.':`Working snapshot: ${data.coverage.covered}/99 combinations completed.`,68,550,1120,86,28,c.amber,true);
}

await fs.mkdir(path.join(build,'renders'),{recursive:true});
await (await PresentationFile.exportPptx(p)).save(path.join(build,'candidate.pptx'));
for(let i=0;i<p.slides.items.length;i++) {
 const s=p.slides.items[i];
 const png=await p.export({slide:s,format:'png',scale:1});
 await fs.writeFile(path.join(build,'renders',`slide-${String(i+1).padStart(2,'0')}.png`),new Uint8Array(await png.arrayBuffer()));
 const layout=await s.export({format:'layout'});await fs.writeFile(path.join(build,'renders',`slide-${i+1}.layout.json`),await layout.text());
}
await fs.writeFile(path.join(build,'deck-meta.json'),JSON.stringify({slides:number,font,dataPath,snapshot:data.generated_at,coverage:data.coverage.covered,final:false},null,2));
const finalizing=process.argv.includes('--finalize');
if(finalizing || process.argv.includes('--validate-draft')) {
 if(finalizing && !data.coverage.ready)throw new Error('Cannot finalize before99-cell coverage');
 const destination=finalizing?path.join(base,'output'):path.join(build,'checked');
 await fs.mkdir(destination,{recursive:true});
 const buildId=Date.now();
 const finalPath=path.join(destination,`${finalizing?'takehome-all-tasks-revised':'working-draft'}-${buildId}.pptx`);
 const receiptPath=path.join(build,`validation-${buildId}.json`);
 const result=await finalizePresentation({explicitTotalSlideCount:19,workspaceDir:base,candidatePath:path.join(build,'candidate.pptx'),finalPath,pythonExecutable:'/Users/sheil/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3',integrityValidatorPath:'/Users/sheil/.codex/plugins/cache/openai-primary-runtime/presentations/26.904.11930/skills/presentations/container_tools/inspect_presentation_package_integrity.py',layoutValidatorPath:'/Users/sheil/.codex/plugins/cache/openai-primary-runtime/presentations/26.904.11930/skills/presentations/container_tools/inspect_presentation_layout_geometry.py',layoutArgs:['--expected-slide-size-emu','12192000,6858000','--validate-heading-fit',...[4,5,6,7,8,9,11,12,13,14].flatMap(n=>['--require-native-table-slide',String(n)])],requiredNativeTableOwnerSlides:[4,5,6,7,8,9,11,12,13,14],requiredNativeChartOwnerSlides:[10],materializeLiteralChartWorkbooks:true,fontPolicy:{basis:'reference',families:[font],referencePath:path.join(base,'output/takehome-all-completed-tasks-99.pptx'),referenceSha256:'4d108ca979ec8061f18778edda2833dede6e4f5bf33c8bfbbc6c75f14f2b43c0'},verifyArtifactToolImport:true,receiptPath});
 console.log(JSON.stringify(result));
 const checked=await PresentationFile.importPptx(await FileBlob.load(finalPath));
 const rendered=path.join(build,finalizing?'final-renders':'checked-renders');
 await fs.mkdir(rendered,{recursive:true});
 for(let i=0;i<checked.slides.items.length;i++) {
  const png=await checked.export({slide:checked.slides.items[i],format:'png',scale:1});
  await fs.writeFile(path.join(rendered,`slide-${String(i+1).padStart(2,'0')}.png`),new Uint8Array(await png.arrayBuffer()));
 }
 await fs.writeFile(path.join(build,finalizing?'final-output.json':'checked-draft.json'),JSON.stringify({path:finalPath,sha256:result.finalSha256,receipt:receiptPath,rendered,dataPath,coverage:data.coverage.covered,snapshot:data.generated_at},null,2));
}
console.log(JSON.stringify({draft:path.join(build,'candidate.pptx'),slides:number,coverage:data.coverage.covered}));
