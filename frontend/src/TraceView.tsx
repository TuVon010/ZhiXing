import type {components} from './api.generated';
import {BillingSummary,BillingDetails} from './PricingSettings';

type TraceDetail=components['schemas']['RunDetail'];
type Entry={id:string;created_at:string;status:string;body:Record<string,any>};
const names:Record<string,string>={MESSAGE_RECEIVED:'接收消息',UNDERSTAND_STARTED:'开始理解',ACTION_PLANNED:'生成行动计划',MODEL_REQUEST:'请求模型',MODEL_RESPONSE:'模型返回',MODEL_FAILED:'模型调用失败',RISK_CHECKED:'风险判断',APPROVAL_REQUESTED:'请求审批',APPROVAL_EDITED:'修改审批参数',APPROVAL_DECIDED:'记录审批决定',WORKFLOW_PAUSED:'等待人工审批',WORKFLOW_RESUMED:'恢复执行',TOOL_CALLED:'调用工具',TOOL_RESULT:'工具执行结果',WORKFLOW_FINISHED:'运行结束',WORKFLOW_FAILED:'运行失败',WORKFLOW_CANCELLED:'取消运行',WORKFLOW_RETRY_REQUESTED:'请求恢复'};
const formatMs=(n:number|null)=>n==null?'未知':n>=1000?(n/1000).toFixed(2)+' s':Math.round(n)+' ms';
const pretty=(value:unknown)=>JSON.stringify(value,null,2);
const jevLabels:Record<string,string>={missing_intent:'遗漏明确请求',unsupported_assumption:'无依据的身份或承诺',needs_clarification:'需要澄清收件人或时间'};
Object.assign(names,{JEV_REQUEST:'请求 Jev 影子评审',JEV_RESPONSE:'Jev 评审返回',JEV_FAILED:'Jev 评审失败（保留原流程）',JEV_SKIPPED:'跳过 Jev 评审'});

export function TraceView({detail}:{detail:TraceDetail}) {
 const t=detail.trace;
 const events=detail.audit as unknown as Entry[];
 const calls=detail.model_calls as unknown as Entry[];
 const reviews=(detail.jev_calls||[]) as unknown as Entry[];
 function download(){const url=URL.createObjectURL(new Blob([pretty(detail)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='zhixing-trace-'+t.trace_id+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
 return <section className="trace-view">
  <div className="trace-heading"><div><small>TRACE ID</small><code>{t.trace_id}</code></div><button onClick={download}>导出 Trace JSON</button></div>
  <BillingSummary value={t.billing}/>
  <div className="trace-metrics">{[['运行耗时（含等待）',formatMs(t.duration_ms)],['模型耗时',formatMs(t.model_latency_ms)],['事件 / 模型调用',`${t.event_count} / ${t.model_call_count}`],['输入 / 输出 Token',`${t.input_tokens??'未知'} / ${t.output_tokens??'未知'}`],['总 Token',t.total_tokens??'未知'],['费用口径','见上方分币种小计']].map(([label,value])=><div key={label}><small>{label}</small><strong>{value}</strong></div>)}</div>
  <h3>执行时间线</h3><div className="timeline trace-timeline">{[...events].reverse().map(e=><details key={e.id} className={'trace-event '+(e.body.error?'trace-error':'')}><summary><span className="trace-dot"/><time>{new Date(e.created_at).toLocaleTimeString('zh-CN',{hour12:false})}</time><strong>{names[e.body.event_type]||e.body.event_type}</strong><span className="trace-node">{e.body.node||'event'}</span><small>{e.body.latency_ms!=null?formatMs(e.body.latency_ms):e.body.status||e.body.decision||''}</small></summary><code className="trace-event-code">{e.body.event_type}</code><pre>{pretty(e.body)}</pre></details>)}</div>
  <h3>关联模型调用</h3>{!calls.length?<p className="trace-muted">这次运行没有调用模型（演示、确定性解析或显式动作计划）。</p>:calls.map(call=><details className="trace-model" key={call.id}><summary>{call.body.model} · {call.status} · {formatMs(call.body.latency_ms)}<small>{call.id}</small></summary><h4>实际请求消息</h4><pre>{pretty(call.body.messages)}</pre><h4>{call.status==='failed'?'错误 / 原始返回':'结构化返回'}</h4><pre>{pretty(call.status==='failed'?{error:call.body.error,response:call.body.raw_response}:call.body.response)}</pre><BillingDetails value={call.body.billing}/><small>usage: {pretty(call.body.usage)}</small></details>)}
  <h3>Jev 影子评审</h3><p className="trace-muted">辅助判断，不改变动作或审批。概率来自模型，尚不代表本项目中文场景的实测准确率。</p>
  {!reviews.length?<p className="trace-muted">本次没有 Jev 记录（默认关闭或旧运行）。</p>:reviews.map(call=><details className="trace-model" key={call.id}><summary>{call.body.response?.model||call.body.model} · {call.status} · {formatMs(call.body.latency_ms)}<small>{call.id}</small></summary>{call.body.response?.answers&&<ul>{Object.entries(call.body.response.answers as Record<string,{noul:number}>).map(([key,answer])=><li key={key}>{jevLabels[key]||key}：{(answer.noul*100).toFixed(1)}%</li>)}</ul>}<p>费用：{call.body.cost==null?'未知':call.body.cost} · 跳过 / 错误：{call.body.reason||call.body.error||'无'}</p><BillingDetails value={call.body.billing}/><h4>评审请求与结果</h4><pre>{pretty(call.body)}</pre></details>)}
  <details><summary>原始运行与完整事件数据</summary><pre>{pretty(detail)}</pre></details>
 </section>;
}
