import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {FileBlob, Presentation, PresentationFile} from '@oai/artifact-tool';
import {resolvePresentationFont, applyPresentationChartFont, finalizePresentation} from '/Users/sheil/.codex/plugins/cache/openai-primary-runtime/presentations/26.904.11930/skills/presentations/container_tools/artifact_tool_utils.mjs';

const build = path.dirname(fileURLToPath(import.meta.url));
const base = path.dirname(build);
const dataOption = process.argv.find(arg => arg.startsWith('--data='));
const dataPath = dataOption ? path.resolve(dataOption.slice('--data='.length)) : path.join(base, 'data/results.json');
const data = JSON.parse(await fs.readFile(dataPath, 'utf8'));
const dataRelativePath = path.relative(base, dataPath);
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
function shortlistModelScore(models) {
 const rows=shortlistFirst.filter(r=>models.includes(r.model));
 return `${rows.filter(r=>r.reward===1).length}/${rows.length}`;
}
const date = data.generated_at.slice(0,16).replace('T',' ')+' UTC';
const foot = `Snapshot ${date} · ${data.coverage.covered}/99 combinations · first counted result per combination`;
let number = 0;
function text(s, value, x, y, w, h, size=28, color=c.ink, bold=false) {
 const q=s.shapes.add({geometry:'textbox',position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});
 q.text=String(value);q.text.style={typeface:font,fontSize:size,color,bold,autoFit:'none'};return q;
}
function line(s,x,y,w,color=c.line) {s.shapes.add({geometry:'rect',position:{left:x,top:y,width:w,height:2},fill:color,line:{fill:'none',width:0}});}
function slide(title, notes='', subtitle='') {
 const s=p.slides.add();s.background.fill=c.white;number++;
 text(s,title,64,40,1152,104,40,c.ink,true);
 if(subtitle)text(s,subtitle,68,142,1144,52,23,c.muted);
 line(s,68,658,1144);text(s,foot,68,675,1100,28,14,c.muted);text(s,number,1175,675,35,28,14,c.muted);
 s.speakerNotes.textFrame.setText(notes+'\n\nData: '+dataRelativePath+' and its adjacent trials.csv. Raw artifact paths and SHA-256 hashes are preserved there.\n'+foot);
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
function resultLabel(id) {const r=trial(id);return r?`${r.model.replace('-5-1','').replace('-5','')}/${r.effort} · ${r.assistant_steps} steps · ${r.reward===1?'pass':'fail'}`:id;}

{
 const s=slide('Rust data and protocol integration', 'Selection remains provisional until the frozen 99-combination coverage gate is met. The scope contains 11 tasks, three models and three effort levels.', 'A pilot for long-horizon software engineering evaluation');
 text(s,'Can an agent preserve a cross-module contract\nwhile implementing a substantial feature?',68,226,1110,134,38,c.ink,true);
 const stats=[`${data.coverage.covered}/99`,`${data.first_results.successful_over_75_steps}/${data.first_results.successful_steps_available}`,'3'];
 const labels=['combinations completed','successful trials above 75 steps','provisional Rust candidates'];
 stats.forEach((v,i)=>{text(s,v,68+i*390,429,360,84,62,c.teal,true);text(s,labels[i],68+i*390,530,350,64,24,c.muted);});
}
{
 const s=slide('The capability is maintaining the whole contract', 'Task sources: candidates_v2/rs-rerun-chunk-optimizer/instruction.md; research/task-revisions/rs-zenoh-timestamp-instrumentation-v3/instruction.md; research/task-revisions/rs-burn-store-pytorch-reader-v4/instruction.md. These tasks cover data representations, streaming or deferred materialization, public interfaces and independent observable behavior.');
 para(s,'Rerun','Transform recording chunks through a bounded stream while preserving every data cell and the required layout.',68,196,350);
 para(s,'Zenoh','Carry timestamp metadata through public APIs, wire encoding, and the publish/receive path.',465,196,350);
 para(s,'Burn','Read real checkpoint formats, retain lazy tensors, and reject malformed or dangerous inputs.',863,196,350);
 line(s,68,498,1144,c.teal);text(s,'The shared demand: implement locally, then verify behavior across module boundaries.',68,533,1120,80,30,c.ink,true);
}
{
 const values=[['Task','Fable','Opus','Sonnet','Covered']];
 for(const task of Object.keys(aliases)) {
  const g=data.tasks[task];
  values.push([aliases[task],...['fable-5-1','opus-5','sonnet-5'].map(m=>`${g.by_model[m]?.passes??0}/${g.by_model[m]?.counted??0}`),`${g.first_results.counted}/9`]);
 }
 const s=slide('Coverage first; reliability needs repeated trials', 'Frozen scope: coverage-scope.json (11 tasks × 3 models × 3 efforts). Each number is passes / first counted results, pooled across effort for description only. Empty cells are not failures. Infrastructure outcomes do not count. Timeouts do count as failures. Repeat trials and superseded revisions are separate. No model ranking or estimated win rate is asserted.', 'Passes / completed first results across medium, high and max effort');
 table(s,values,68,184,1144,452,[540,145,145,145,169],19);
}
{
 const ranked=Object.entries(data.tasks).filter(([,g])=>g.first_results.successful_steps_available>0).sort((a,b)=>b[1].first_results.median_successful_assistant_steps-a[1].first_results.median_successful_assistant_steps);
 const s=slide('Successful runs often take more than 75 steps','An agent step is one assistant turn in the saved ATIF trajectory, not one shell command or one human work unit. Tool calls and elapsed time are separate measurements. Median is across successful first counted results currently available for each task; failures are excluded from this chart. Incomplete coverage can move these medians.', `Median turns on success · ${data.first_results.successful_over_75_steps}/${data.first_results.successful_steps_available} successful trials exceed 75 turns`);
 const ch=s.charts.add('bar',{position:{left:68,top:205,width:1144,height:425},categories:ranked.map(([k])=>aliases[k]),series:[{name:'Median assistant turns',values:ranked.map(([,g])=>g.first_results.median_successful_assistant_steps),fill:c.teal}],barOptions:{direction:'bar',grouping:'clustered',gapWidth:50},hasLegend:false,xAxis:{visible:false,majorGridlines:null,textStyle:{fontSize:18,fill:c.ink}},yAxis:{textStyle:{fontSize:18,fill:c.ink},line:{fill:c.line,width:1}},dataLabels:{showValue:true,position:'outEnd',textStyle:{fontSize:18,fill:c.ink}},chartFill:c.white,plotAreaFill:c.white});
 applyPresentationChartFont(ch,{fontFamily:font});
}
{
 const s=slide('Three Rust candidates with observed Sonnet failures','Provisional selection is based on task coherence, reproducibility, observed work and audited contract failures. Raw pass counts do not by themselves establish learning headroom. In these three original first-result cohorts, Fable and Opus pass all 18 settings combined; Sonnet passes five of nine. This supports a pilot focused on Sonnet, not demonstrated headroom for every evaluated model. Zenoh v3 outcomes remain historical; corrected-grader replays are separate and are not new model trials. All three have successful model completions. Steps on success are median assistant turns among successful first results. Exact controls and failure artifacts are described in evidence/shortlist-cases.json.','Original first-result cohorts · passes and median steps on success');
 const vals=[['Candidate','Pass / first trials','Steps on success','Reason to retain']];
 const reasons=['Streaming layout + data preservation','Wire protocol + public API integration','Format compatibility + bounds validation'];
 selected.forEach((task,i)=>{const g=data.tasks[task].first_results;vals.push([aliases[task],`${g.passes}/${g.counted}`,`${g.median_successful_assistant_steps}`,reasons[i]]);});
 table(s,vals,68,194,1144,288,[330,200,180,434],24);
 text(s,'Observed failures are concentrated in Sonnet',68,516,1100,40,27,c.amber,true);
 text(s,`Fable and Opus: ${shortlistModelScore(['fable-5-1','opus-5'])} passes. Sonnet: ${shortlistModelScore(['sonnet-5'])} passes.\nRepeated trials and corrected Zenoh regrades remain separate.`,68,565,1110,75,26,c.ink);
}
{
 const s=slide('Rerun: a concrete integration failure','Audited trial EeEppp5: Sonnet/medium, 138 assistant steps, reward 0. The diagram shows actual rows-per-output-chunk [6,2] versus expected [4,4] in chunks_that_do_not_fit_together_emit_alone_in_order (optimizer_contract.rs:410). This is not evidence of data loss. Existing regression suites pass; five feature/contract tests fail. Quote: native assistant turn138 / ATIF step140. Compare Sonnet/max iUZ6GKK (383 steps, reward 1). Full hashes and instruction/test links: evidence/shortlist-cases.json. Separate repeat YSSc6xQ (Sonnet/medium) takes 124 turns and fails nine of 96 tests. It measures Arc<Chunk>, whose attributed size varies with reference count, instead of the specified decoded Chunk. This is consistent with the packing and splitting failures; no replay proves it is the sole cause. Opus/max YSPYcnB passes in 132 turns with 148 tool calls. See evidence/repeat-case-audit-20260914T1018.json and evidence/repeat-case-root-review-20260914T1033.json. These repeats do not replace the first-result cohort.');
 function chunks(label,values,y,color) {
  text(s,label,68,y,510,34,25,c.ink,true);let x=68;
  for(const value of values) {
   const width=value*55;
   s.shapes.add({geometry:'rect',position:{left:x,top:y+44,width,height:57},fill:color,line:{fill:'none',width:0}});
   text(s,String(value),x+12,y+54,width-18,40,25,c.white,true);x+=width+12;
  }
 }
 chunks('Returned layout · rows per chunk',[6,2],191,c.amber);
 chunks('Required layout · rows per chunk',[4,4],325,c.teal);
 text(s,'“All existing tests for the three crates pass, plus my own smoke tests”',660,204,548,109,29,c.ink,true);
 text(s,'The hidden verifier found five split, packing and row-limit failures. The existing regression suites still passed.',660,345,548,112,26,c.ink);
table(s,[['Same task, different effort','Observed result'],[resultLabel('EeEppp5'),'Layout contract failures'],[resultLabel('iUZ6GKK'),'All verifier groups passed']],68,479,1144,140,[590,554],23);
}
{
 const s=slide('Zenoh: the public type was wrong after 377 steps','Audited Sonnet/medium YtCswNv uses ZenohIdProto in TimestampContext.zid; instruction explicitly requires ZenohId. This is distinct from the old hidden-import test mismatch corrected in revision v3. Sonnet/max gSYDMEG passes the historical v3 verifier in 626 steps. The separate revised validation task adds direct malformed/boundary-stack and admin-space Receive tests, plus a reference fix preserving the received timestamp stack in replies. Its confirmed controls are oracle 1 and no-op 0. The completed quality002 review passes all 11 criteria in the officially collected report; this is an automated review judgment, not proof of exhaustive boundary coverage. All eight focused diagnostics are complete: six targeted omissions fail their intended assertions, and both exact saved submissions pass all seven new tests. These checks used the cached build image. Completion of all nine full saved-source regrades on the normal verifier image remains pending. See evidence/zenoh-eight-focused-root-review-20260914T0948.json. Original v3 scores remain unchanged. See evidence/zenoh-quality-final-root-review-20260914T0841.json, evidence/zenoh-continuation004-root-review-20260914T0852.json, and research/zenoh-coverage-followup-2026-09-14/reviews-final-002/summary.json. A separate Sonnet/max repeat, 6nTASYR, passes the historical v3 verifier in 621 turns. This is not a corrected-v4 grade. See evidence/repeat-case-audit-20260914T1018.json and evidence/repeat-case-root-review-20260914T1033.json.','Revised-task quality: 11/11; saved-source regrades pending');
 para(s,'Contract boundary','TimestampContext.zid must expose ZenohId. A wire representation is not interchangeable with the required public type.',68,198,540);
 para(s,'Why it matters','Downstream consumers compile against the stated API. A mostly complete implementation still fails that integration boundary.',677,198,535);
 text(s,'“Everything is green with both feature configurations.”',68,402,1120,42,24,c.muted);
table(s,[['Sonnet effort','Observed result'],['Medium · 377 steps','Required public-type mismatch'],['Max · 626 steps','Passes the historical v3 verifier']],68,479,1144,140,[590,554],23);
}
{
 const s=slide('Burn: functional and source-constraint outcomes','Revision v4. Sonnet/medium vXNtQ5E failed FRAME/TAR/storage-bounds functionality in 42 steps. Sonnet/high BrRKozk completed in 251 steps and passed the functional groups, but retained a prohibited fixture path in baseline-derived inline test code. Raw reward remains 0; this outcome is not a functional failure or evidence of cheating. Sonnet/max X6pyirF passed all eight verifier groups in 323 steps, including 62 matrix cases and 32 rejection cases. Fable/max and Opus/max both pass in 158 steps. See evidence/burn-v4-sonnet-max.json and evidence/shortlist-cases.json. Separate repeat UDNoWje takes 191 turns and passes 217 Rust tests and the 62-case fixture matrix, but an allocation abort on a 125-byte doubling-bomb fixture violates the explicit R12 requirement to return an error. Exact allocation origin was not traced. Sonnet/max kYchPug and Fable/max RBjsods later pass all eight groups in 274 and 171 turns. These repeats remain separate from the first-result cohort. See evidence/repeat-case-audit-20260914T0923.json, evidence/repeat-case-root-review-20260914T0929.json, evidence/repeat-case-audit-20260914T1018.json and evidence/repeat-case-root-review-20260914T1033.json.');
 table(s,[['Observation','Interpretation'],['Sonnet medium · 42 steps','Functional parser and validation gaps'],['Sonnet high · 251 steps','Functional checks passed; source constraint failed'],['Sonnet max · 323 steps','Clean pass across all verifier groups'],['Fable / Opus max · 158 steps each','Additional clean passes']],68,194,1144,350,[460,684],25);
 text(s,'The task has depth. These outcomes alone do not prove that every failure requires long-horizon reasoning.',68,577,1120,66,26,c.ink,true);
}
{
 const s=slide('Failure validity and task headroom','SQLite NUL audit: research/sqlite-nul-validity-audit-2026-09-14/{README.md,results.json,sqlite-default-proof.json}. 30 counted attempts span nine cells in two campaigns; all fail NUL, only three exclusively. NUL is valid under the all-strings contract and SQLite supports embedded NUL: https://www.sqlite.org/nulinstr.html . Huey/SQLite held revisions are excluded from the 99-cell gate. Burn Debug and Zenoh import fixes had separate validated revisions.');
 para(s,'A real failure can be a weak headline','SQLite’s NUL default is a valid literal-string case. All 30 attempts missed it, but one serializer edge case is not 30 independent failure modes.',68,198,540);
 para(s,'A test can also overreach','Hidden internal imports, an unstated Debug bound, and an empty-rename rejection needed correction. Corrected revisions keep separate cohorts.',677,198,535);
 text(s,'Classify each outcome: implementation defect · stated constraint · test/spec mismatch · infrastructure.',68,550,1120,89,28,c.teal,true);
}
{
 const s=slide('External evidence motivates a self-verification probe','SWE-Marathon paper, June5 2026, abstract: https://arxiv.org/abs/2606.07682v1 . It reports20tasks and fewer than30% solved by the evaluated frontier coding agents; this is a paper-era result, not a current leaderboard or a result for our three model aliases. Short verbatim quote below is from the abstract. These tasks, budgets and metrics differ from our pilot. Our inference: a narrower task family can test whether agents verify the complete feature contract.');
 text(s,'20',68,206,320,95,70,c.teal,true);text(s,'tasks in SWE-Marathon',68,315,490,52,28,c.ink);
 text(s,'<30%',677,206,400,95,70,c.teal,true);text(s,'solved by agents evaluated in the paper',677,315,535,78,28,c.ink);
 text(s,'“poor self-verification, self-reported infeasibility, and premature termination”',68,438,1120,94,31,c.ink,true);
 text(s,'Our probe narrows this to Rust implementations that pass local checks yet violate explicit integration contracts.',68,555,1120,80,28,c.muted);
}
{
 const s=slide('What the pilot establishes','Sources: https://arxiv.org/abs/2509.16941v2 (SWE-Bench Pro, abstract) and https://arxiv.org/abs/2503.14499v4 (METR, abstract). SWE-Bench Pro characterizes complex software tasks involving hours to days of human work. METR defines its horizon using measured human time and a success threshold. This pilot has no human time baseline, so assistant-turn counts cannot be converted into that metric. Public upstream provenance and recent commits do not prove that a model has never seen a task or its implementation.');
 para(s,'What the observations establish','Several completed attempts took many assistant turns. Some submissions fail explicit contracts. Successful completions show that those tasks are solvable.',68,196,540);
 para(s,'What remains unmeasured','Reliable per-cell pass rates, causal effects of effort, broad model ordering, human completion time, and gains from training.',677,196,535);
 text(s,'SWE-Bench Pro: “patches across multiple files and substantial code modifications.”',68,509,1120,50,25,c.muted);
 text(s,'METR’s time horizon is human-time calibrated; our step count is a different measurement.',68,576,1120,60,25,c.muted);
}
{
 const s=slide('Scaling to 1,000 accepted tasks','Proposed scaling design, not a measured production yield or budget. Source candidates from public Rust data, storage, serialization and protocol repositories. Deduplicate related PR stacks and templates before splitting. Hold out repositories or families to test transfer. A 20% acceptance scenario needs 5,000 mined candidates for 1,000 accepted tasks; actual yield must be measured in a staged pilot.');
 const labels=['Mine issues + PR stacks','Compose dependent changes','Audit properties + controls','Hold out task families'];
 labels.forEach((v,i)=>{text(s,String(i+1).padStart(2,'0'),68+i*296,211,270,56,42,c.teal,true);text(s,v,68+i*296,284,260,100,28,c.ink,true);});
 text(s,'Public sources: Rerun · Burn · Zenoh',68,397,1120,46,25,c.muted);
text(s,'Pilot batches of 25 → 100 → 1,000 accepted tasks',68,451,1120,64,34,c.ink,true);
 text(s,'Track acceptance yield, authoring time, task validity, runtime cost and distinct failure mechanisms at each stage.',68,535,1120,98,28,c.ink);
}
{
 const s=slide('Task pack and next experiment','Retain the three Rust candidates after exact-package reference/no-op controls and required task checks. Preserve raw Harbor job/trial structure and bytes. Keep discarded candidates in archive. Repeat trials within fixed task/model/effort cells to estimate pass rates, separating infrastructure outcomes and task revisions.');
 para(s,'Evidence provided','Frozen first-result selection, raw artifact hashes, step and cost measurements, failure-to-contract mappings, and successful contrast trajectories.',68,198,540);
 para(s,'Next experiment','Repeat each fixed task/model/effort combination. Estimate pass rates with uncertainty, then compare recurring failures against the task contract.',677,198,535);
 text(s,data.coverage.ready?'First-result coverage is complete at 99/99. The repeat-trial sweep continues.':`Working snapshot: ${data.coverage.covered}/99 combinations completed. The sweep continues.`,68,550,1120,86,28,c.amber,true);
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
 const finalPath=path.join(destination,`${finalizing?'takehome-rust-integration':'working-draft'}-${buildId}.pptx`);
 const receiptPath=path.join(build,`validation-${buildId}.json`);
 const result=await finalizePresentation({workspaceDir:base,candidatePath:path.join(build,'candidate.pptx'),finalPath,pythonExecutable:'/Users/sheil/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3',integrityValidatorPath:'/Users/sheil/.codex/plugins/cache/openai-primary-runtime/presentations/26.904.11930/skills/presentations/container_tools/inspect_presentation_package_integrity.py',layoutValidatorPath:'/Users/sheil/.codex/plugins/cache/openai-primary-runtime/presentations/26.904.11930/skills/presentations/container_tools/inspect_presentation_layout_geometry.py',layoutArgs:['--expected-slide-size-emu','12192000,6858000','--validate-heading-fit',...[3,5,6,7,8].flatMap(n=>['--require-native-table-slide',String(n)])],requiredNativeTableOwnerSlides:[3,5,6,7,8],requiredNativeChartOwnerSlides:[4],materializeLiteralChartWorkbooks:true,fontPolicy:{basis:'design',families:[font]},verifyArtifactToolImport:true,receiptPath});
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
