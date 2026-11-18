import {DOMAIN} from '../constants/domain.js';
import {store,load,loadRollRows} from '../stores/research.js';
import {rollBatches,rollRuns} from '../api/research.js';
import {table} from '../components/common/table.js';
import {status} from '../components/common/status.js';
import {esc,fmt} from '../utils/format.js';
import {bindForm} from '../hooks/forms.js';

const PROCESS_LABEL={A:'A 组',B:'B 组'};
function batchSelect(){return `<label>整卷批次<select id="roll-batch-select" name="batch_id">${store.rollBatches.length?store.rollBatches.map(b=>`<option value="${b.id}" ${b.id===store.rollBatchId?'selected':''}>${esc(b.name)} · v${b.version} · ${b.roll_count}卷</option>`).join(''):'<option value="">请先创建批次</option>'}</select></label>`;}

function recordForm(){return `<h2 id="roll-row-heading">录入试封卷</h2><form id="roll-row-form" class="inline-form"><label>班次<input name="shift" placeholder="如 早班" required></label><label>卷号<input name="roll_no" placeholder="如 ROLL-001" required></label><label>工艺<select name="process"><option value="A">A 组</option><option value="B">B 组</option></select></label><label>合格件数<input name="qualified" type="number" min="0" step="1" required></label><label>试封件数<input name="pieces" type="number" min="1" max="100000" step="1" required></label><button type="submit">保存整卷</button><button type="button" id="roll-cancel-edit" hidden>取消编辑</button></form>`;}

function batchSummary(){const b=store.rollBatches.find(x=>x.id===store.rollBatchId);if(!b)return '';const rows=store.rollRows;const shifts=[...new Set(rows.map(r=>r.shift))];const bRolls=rows.filter(r=>r.process==='B').length;const aRolls=rows.length-bRolls;const pieces=[...new Set(rows.map(r=>r.pieces))];return `<div class="strip"><span>批次版本 v${b.version}</span><span>${rows.length} 卷</span><span>A 组 ${aRolls} 卷 / B 组 ${bRolls} 卷</span><span>${shifts.length} 个班次</span><span>每卷 ${pieces.length===1?pieces[0]+' 件':pieces.length?'件数不一致':'—'}</span></div>`;}

export function rollRunTable(rows){return table(['编号 / 判优','状态','冻结版本','纳入卷数','单侧p值'],rows.map(r=>[{html:`<a href="#roll-results/${r.id}">#${r.id} ${esc(r.name)}</a>`},{html:status(r.status)},'v'+r.batch_version,r.included_rolls,r.result?fmt(r.result.metrics['单侧p值']):(r.status==='refused'?esc(r.error):'—')]));}

export async function render(){
  await loadRollRows();
  const writable=localStorage.getItem('research-role')!=='viewer';
  const hasBatch=store.rollBatches.length>0;
  return `<div class="eyebrow">整卷 / WHOLE-ROLL REGISTER</div><h1>${DOMAIN.roll_study_label}与整卷判优</h1><p class="lead">${DOMAIN.roll_algorithm}</p><div class="method-note">${DOMAIN.roll_note}<br>同一冻结版本内查重与计算；班次缺组、卷号重复、每卷件数不一致一律拒绝。补录以版本号防止与冻结快照错位。</div><div class="toolbar">${batchSelect()}</div>${hasBatch?batchSummary():''}${writable?`<details><summary>新建整卷批次</summary><form id="roll-batch-form" class="inline-form"><label>批次名称<input name="name" required minlength="2" value="${esc(DOMAIN.roll_names[store.rollBatches.length%DOMAIN.roll_names.length])}"></label><label>说明<input name="note"></label><button type="submit">保存批次</button></form></details>${hasBatch?recordForm():''}`:''}<h2>整卷明细 <small>${store.rollRows.length} 卷 · 卷内试件不拆开</small></h2>${table(['班次','卷号','工艺','合格件数','试封件数',...(writable?['操作']:[])],store.rollRows.map(row=>[row.shift,row.roll_no,PROCESS_LABEL[row.process]||row.process,row.qualified,row.pieces,...(writable?[{html:`<button class="text" data-roll-edit="${row.id}">编辑</button> <button class="text" data-roll-delete="${row.id}">移除</button>`}]:[])]))}${hasBatch&&writable?`<details><summary>冻结整卷并判优</summary><form id="roll-run-form" class="worksheet"><label>本次判优名称<input name="name" required minlength="2" value="${esc(DOMAIN.roll_names[0])} · 判优"></label><label>显著性 α (0.001–0.2)<input name="alpha" type="number" step="any" value="${DOMAIN.roll_defaults.alpha}" required></label><p>冻结当前批次的全部整卷（版本 v${store.rollBatches.find(x=>x.id===store.rollBatchId)?.version??0}）与参数，生成 SHA256；10 秒内精确给出分数，超时拒绝判优。</p><button type="submit">冻结并排队判优 →</button></form></details>`:''}<h2>判优记录</h2>${rollRunTable(store.rollRuns)}`;
}

export function bind(refresh){
  const sel=document.getElementById('roll-batch-select');
  if(sel)sel.onchange=()=>{store.rollBatchId=+sel.value;refresh();};
  let editing=null;
  bindForm('roll-batch-form',async data=>{const batch=await rollBatches.create(data);store.rollBatchId=batch.id;await load();refresh();});
  bindForm('roll-row-form',async data=>{
    const batch=store.rollBatches.find(b=>b.id===store.rollBatchId);
    if(!batch)throw new Error('请先创建整卷批次');
    await rollBatches.save(store.rollBatchId,{shift:data.shift,roll_no:data.roll_no,process:data.process,qualified:+data.qualified,pieces:+data.pieces,expected_version:batch.version},editing);
    await load();refresh();
  });
  bindForm('roll-run-form',async data=>{if(!store.rollRows.length)throw new Error('先录入至少一卷试封卷');const r=await rollRuns.create({batch_id:store.rollBatchId,name:data.name,parameters:{alpha:+data.alpha}});store.rollRunId=r.id;await load();location.hash='roll-results/'+r.id;});
  document.querySelectorAll('[data-roll-edit]').forEach(el=>el.onclick=()=>{const row=store.rollRows.find(r=>r.id===+el.dataset.rollEdit),form=document.getElementById('roll-row-form');editing=row.id;form.elements.shift.value=row.shift;form.elements.roll_no.value=row.roll_no;form.elements.process.value=row.process;form.elements.qualified.value=row.qualified;form.elements.pieces.value=row.pieces;document.getElementById('roll-row-heading').textContent='编辑 '+row.roll_no;document.getElementById('roll-cancel-edit').hidden=false;form.scrollIntoView({behavior:'smooth',block:'center'});});
  const cancel=document.getElementById('roll-cancel-edit');
  if(cancel)cancel.onclick=()=>refresh();
  document.querySelectorAll('[data-roll-delete]').forEach(el=>el.onclick=async()=>{const batch=store.rollBatches.find(b=>b.id===store.rollBatchId);try{await rollBatches.remove(store.rollBatchId,+el.dataset.rollDelete,batch.version);await load();refresh();}catch(e){document.getElementById('notice').textContent=e.message;}});
}
