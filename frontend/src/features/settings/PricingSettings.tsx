import {useEffect,useState} from 'react';
import type {components} from '../../shared/api/generated';

type Profile=components['schemas']['PricingProfile'];
type Api=(path:string,body?:unknown)=>Promise<any>;
const rates=[['input','未命中输入'],['cached_input','缓存命中输入'],['output','输出'],['peak_input','高峰未命中输入'],['peak_cached_input','高峰缓存命中输入'],['peak_output','高峰输出']];
const sources:Record<string,string>={default:'默认',custom:'自定义',legacy_env:'旧环境配置',unknown:'未知'};

export function PricingSettings({api}:{api:Api}){
 const [profiles,setProfiles]=useState<Profile[]>([]),[selected,setSelected]=useState(''),[draft,setDraft]=useState<Record<string,any>>({}),[advanced,setAdvanced]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
 const current=profiles.find(p=>p.id===selected);
 function choose(p:Profile){setSelected(p.id);setDraft({...p.overrides});setAdvanced(JSON.stringify(Object.fromEntries(Object.entries(p.overrides).filter(([k])=>!rates.some(([key])=>key===k)&&!['currency','peak_enabled'].includes(k))),null,2));setNotice('');setError('')}
 async function load(){try{const rows:Profile[]=await api('pricing');setProfiles(rows);const p=rows.find(r=>r.id===selected)||rows[0];if(p)choose(p)}catch(e){setError(String(e))}}
 useEffect(()=>{void load()},[]);
 async function save(reset=false){if(!current)return;setBusy(true);setError('');setNotice('');try{
  const extra=reset?{}:JSON.parse(advanced);
  if(!extra||Array.isArray(extra)||typeof extra!=='object')throw new Error('高级设置必须是 JSON 对象');
  if(Object.keys(extra).some(k=>rates.some(([key])=>key===k)||['currency','peak_enabled'].includes(k)))throw new Error('价格、币种和计价方式请在表单中填写');
  const overrides=reset?{}:{...extra,...Object.fromEntries([...rates.map(([key])=>key),'currency','peak_enabled'].map(key=>[key,draft[key]===''||draft[key]===undefined?null:draft[key]]))};
  const updated:Profile=await api('pricing/'+current.id,{version:current.version,overrides});setProfiles(rows=>rows.map(p=>p.id===updated.id?updated:p));choose(updated);setNotice(reset?'已恢复默认计价':'计价已保存；后续调用生效，历史记录保持原价');
 }catch(e){setError(String(e))}finally{setBusy(false)}}
 return <section className="panel pricing-settings"><h3>模型计价</h3><p>留空使用默认值，可以只改一项。填写 0 表示零价。金额单位为每百万 Token；保存仅影响后续调用。</p>
 {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
 <label>计价模型 <select aria-label="计价模型" value={selected} onChange={e=>{const p=profiles.find(p=>p.id===e.target.value);if(p)choose(p)}}>{profiles.map(p=><option key={p.id} value={p.id}>{p.label}{p.active?' · 当前使用':''}</option>)}</select></label>
 {current&&<><p>配置版本 {current.version} · 默认价版本 {current.default_version} · {current.source?<a href={current.source} target="_blank" rel="noreferrer">价格来源</a>:'没有已核实的默认价格，请填写币种与单价'}</p>
 <form onSubmit={e=>{e.preventDefault();void save()}}>
 <div className="pricing-grid"><label>币种<input aria-label="计价币种" value={draft.currency??''} placeholder={String(current.defaults.currency??'例如 CNY / USD')} pattern="[A-Z]{3}" onChange={e=>setDraft({...draft,currency:e.target.value.toUpperCase()})}/><small>生效：{String(current.effective.currency??'未知')} · {sources[current.field_sources.currency]}</small></label>
 <label>时段计价<select aria-label="时段计价" value={draft.peak_enabled===undefined?'':String(draft.peak_enabled)} onChange={e=>setDraft({...draft,peak_enabled:e.target.value===''?undefined:e.target.value==='true'})}><option value="">默认（{current.defaults.peak_enabled?'分时计价':'统一价格'}）</option><option value="true">分时计价</option><option value="false">统一价格</option></select><small>统一价格使用前三项；分时计价前三项为空闲价。</small></label>
 {rates.map(([key,label])=><label key={key}>{label}<input aria-label={label+'单价'} type="number" min="0" max="1000000000" step="any" value={draft[key]??''} placeholder={String(current.defaults[key]??'无默认值')} onChange={e=>setDraft({...draft,[key]:e.target.value})}/><small data-testid={'effective-'+key}>生效：{String(current.effective[key]??'未知')} · {sources[current.field_sources[key]]}</small></label>)}</div>
 <details><summary>高级时段设置</summary><p>JSON 中只填写需要覆盖的字段。删除字段或设为 null 恢复默认；空数组表示不设置任何日期或时段。星期一为 0，星期日为 6；时段左闭右开。日历年份表示已确认完整节假日清单的年份。</p><textarea aria-label="高级计价设置" rows={10} value={advanced} onChange={e=>setAdvanced(e.target.value)}/><p>可设置 timezone、peak_windows、peak_weekdays、holiday_dates、calendar_years。</p><details><summary>当前生效时段与日历</summary><pre>{JSON.stringify(Object.fromEntries(Object.entries(current.effective).filter(([k])=>!rates.some(([key])=>key===k))),null,2)}</pre></details><p>内置 2026 年放假安排用于本地估算；未来年份未配置时显示费用区间。{current.calendar_source&&<a href={current.calendar_source} target="_blank" rel="noreferrer">日历来源</a>}</p></details>
 <div className="actions"><button className="primary" disabled={busy} type="submit">保存计价</button><button disabled={busy} type="button" onClick={()=>void save(true)}>恢复默认计价</button><button disabled={busy} type="button" onClick={()=>void load()}>重新加载计价</button></div></form><p>更换币种需填写全部生效单价，不自动换汇。费用为估算；未知模型和第三方中转不套用官方价格。</p></>}
 </section>
}

export function BillingSummary({value}:{value:any}){
 if(!value)return null;
 return <div className="billing-summary"><strong>费用估算（已知小计）</strong><p>{Object.entries(value.known_subtotals||{}).map(([currency,amount])=>`${currency} ${amount}`).join(' · ')||'暂无已知费用'} · 未知 {value.unknown_calls??0} 次 · 区间 {value.range_calls??0} 次</p><p>可观测输入的缓存命中率：{value.cache_hit_rate==null?'未知':(value.cache_hit_rate*100).toFixed(2)+'%'}（有缓存字段的输入 {value.cache_observed_input_tokens??0} Token）</p></div>
}

export function BillingDetails({value}:{value:any}){
 if(!value)return <p>此记录未保存计价明细，不按新配置回填历史费用。</p>;
 const u=value.usage||{};
 return <div className="billing-details"><h4>计价明细</h4><p>{value.currency||'币种未知'} {value.amount??(value.minimum!=null?`${value.minimum} ～ ${value.maximum}`:'费用未知')} · {value.period==='peak'?'高峰':value.period==='offpeak'?'空闲 / 统一价格':'时段未知'}</p><p>输入 {u.input_tokens??'未知'} · 缓存命中 {u.cached_tokens??'未知'} · 未命中 {u.uncached_tokens??'未知'} · 输出 {u.output_tokens??'未知'} Token</p><p>{value.reason}</p><details><summary>调用时价格与来源快照</summary><pre>{JSON.stringify(value.snapshot,null,2)}</pre></details></div>
}
