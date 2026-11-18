import {store} from '../stores/research.js';
import {rollRuns} from '../api/research.js';
import {esc,fmt} from '../utils/format.js';
import {table} from '../components/common/table.js';
import {status} from '../components/common/status.js';
import {rollRunTable} from './rolls.js';

export async function render(id){
  if(!id)return `<h1>整卷判优结果</h1><p>选择一次判优查看冻结整卷、精确分数与纳入卷数。</p>${rollRunTable(store.rollRuns)}`;
  const r=await rollRuns.get(id);store.rollRunId=r.id;
  const refused=r.status==='refused';
  return `<div class="eyebrow">整卷 / PERMUTATION VERDICT · RUN ${r.id}</div><h1>${esc(r.name)} ${status(r.status)}</h1><div class="checksum">INPUT SHA256 ${r.checksum}</div><p>冻结于批次版本 v${r.batch_version} · 纳入 ${r.included_rolls} 卷 · ${esc(r.created_at)}</p>${refused?`<div class="method-note"><strong>已拒绝判优</strong><p>${esc(r.error)}</p><p>整卷判优要求 40 卷以内、每卷不超过十万件，且须在 10 秒内精确给分；超出边界不给出优劣结论。请修正班次分组、卷号或件数后重新冻结。</p></div>`:r.status==='failed'?`<div class="error">计算失败：${esc(r.error)}。请修正数据后创建新判优。</div>`:r.result?`<section class="result-intro"><strong>${esc(r.result.interpretation)}</strong><p>${esc(r.result.caveat)}</p></section><h2>核心数值 <small>纳入 ${r.result.included_rolls} 卷</small></h2>${table(['业务指标','计算值'],Object.entries(r.result.metrics).map(([k,v])=>[k,fmt(v)]))}<h2>${esc(r.result.table_title||'整卷明细')}</h2>${table(r.result.columns,r.result.rows)}<p class="checksum">计算方向 ${esc(r.result.method?.orientation||'')} · 用时 ${esc(r.result.method?.elapsed_seconds??'')} 秒 · 精确整数枚举，无随机近似</p><details><summary>本次冻结的参数与整卷快照</summary><pre>${esc(JSON.stringify({parameters:r.parameters,snapshot:r.snapshot},null,2))}</pre></details>`:'<p class="method-note">判优任务正在队列中。此页每2秒更新，结果写入数据库后展示。</p>'}`;
}
export function bind(){}
