import { useCallback, useEffect, useRef, useState } from "react";
import { useWorkspace } from "../../app/workspace/context";

const names: Record<string, string> = { category: "分类", spam_label: "垃圾判断", priority: "优先级", needs_reply: "回复意图", baseline: "只看邮件", preferences: "加入偏好", examples: "加入纠正案例", current: "当前版本", candidate: "候选版本", queued: "排队中", running: "评测中", completed: "已完成", failed: "失败", unverified: "未真实验证", cancelled: "已取消", published: "已发布", optimization: "优化集", holdout: "保留集" };
const percent = (value: any) => typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "未测量";
const labelValue = (field: string, value: unknown) => {
  if (typeof value === "boolean") return value ? "是" : "否";
  if (field === "priority") return ({ high: "高", normal: "普通", low: "低" } as Record<string, string>)[String(value)] || String(value);
  return ({ work: "工作", personal: "个人", ad: "广告/营销", notification: "系统通知", other: "其他", normal: "正常", spam: "垃圾", uncertain: "待复核" } as Record<string, string>)[String(value)] || String(value);
};

export function ClassificationSettings() {
  const { account, api, act, openMail, setTrace } = useWorkspace();
  const [data, setData] = useState<any>(null);
  const [split, setSplit] = useState("optimization");
  const [limit, setLimit] = useState(10);
  const [report, setReport] = useState<any>(null);
  const currentAccount = useRef(account); currentAccount.current = account;
  const prefix = `mail/accounts/${account}/classification`;
  const load = useCallback(async () => {
    if (!account) return;
    const value = await api(`mail/accounts/${account}/classification`);
    if (currentAccount.current === account) { setData(value); setReport((old: any) => old ? value.evaluations.find((r: any) => r.id === old.id) || old : null); }
  }, [account, api]);
  useEffect(() => {
    setData(null); setReport(null);
    if (!account) return;
    void act(load);
  }, [account, load]);
  const visible = data?.account_id === account ? data : null;
  const pending = visible?.evaluations?.some((r: any) => ["queued", "running"].includes(r.status)) || visible?.candidates?.some((r: any) => r.status === "queued");
  useEffect(() => {
    if (!pending) return;
    const timer = setInterval(() => { void load().catch(() => {}); }, 4000);
    return () => clearInterval(timer);
  }, [pending, load]);
  const command = (path: string, body: any, message: string) => void act(async () => { await api(`${prefix}/${path}`, body); await load(); }, message);
  const openReport = (id: string) => void act(async () => {
    const value = await api(`${prefix}/evaluations/${id}`);
    if (currentAccount.current === account) setReport(value);
  });
  const openTrace = (id: string) => void act(async () => {
    const value = await api(`mail/traces/${id}`);
    if (currentAccount.current === account) setTrace(value);
  });
  return <section className="panel classification-settings">
    <div className="mail-sectionbar"><h3>分类反馈与效果验证</h3><button disabled={!account} onClick={() => void act(load)}>刷新标注与评测</button></div>
    {!account ? <p>请先选择一个邮箱。标注、参考案例和分类版本按邮箱隔离。</p> : !visible ? <p>正在读取当前邮箱的标注…</p> : <>
      <p>调整 AI 判断会保存确认标注；仅确认的字段参与评测。长期偏好仍需在记忆页确认，模型参数不会被训练。</p>
      <div className="mail-correction-grid"><label>参考已确认的相似案例<input type="checkbox" checked={visible.config.enabled} onChange={e => command("config", { ...visible.config, enabled: e.target.checked }, "已更新后续分类参考设置")} /></label><label>每次最多参考案例<select value={visible.config.max_examples} onChange={e => command("config", { ...visible.config, max_examples: Number(e.target.value) }, "已更新案例数量")}>{[0, 1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n} 条</option>)}</select></label></div>
      <p>确认标注 {visible.total} 封 · 优化集 {visible.split_counts.optimization} · 保留集 {visible.split_counts.holdout} · 当前版本 {visible.policy.id === "builtin-v2" ? "内置版本" : `v${visible.policy.body.version}`}</p>
      <button onClick={() => command("labels/backfill", {}, "已将旧纠正中的明确字段导入标注")}>导入已有人工纠正</button>
      <details><summary>查看确认标注（最近 30 封）</summary>{visible.items.map((r: any) => <div className="mail-list-row" key={r.id}><div><strong>{r.body.mail.subject}</strong><p>{Object.entries(r.body.labels).map(([k, v]) => `${names[k] || k}：${labelValue(k, v)}`).join(" · ")}</p><small>{names[r.body.split]} · 标注 v{r.body.revision}</small></div><div className="actions"><button onClick={() => void openMail(r.body.message_id)}>原邮件</button><button onClick={() => command(`labels/${r.id}/withdraw`, {}, "已移出案例参考与评测，当前邮件纠正仍保留")}>移出标注集</button></div></div>)}</details>
      <hr /><h4>用同一批标注对比分类效果</h4>
      <p>优化集用于调整，保留集不进入参考案例。系统隔离线程和结构相同的模板；纠正集偏向错误案例，请同时抽查放行和过滤的邮件。</p>
      <div className="actions"><label>评测分组<select value={split} onChange={e => setSplit(e.target.value)}><option value="optimization">优化集</option><option value="holdout">保留集</option></select></label><label>最多邮件数<input type="number" min={1} max={30} value={limit} onChange={e => setLimit(Math.max(1, Math.min(30, Number(e.target.value) || 1)))} /></label><button onClick={() => command("evaluations", { split, limit }, "分类对比已排队；使用真实 API 时会计入模型预算")}>开始三组对比</button><button disabled={visible.split_counts.optimization < 5} onClick={() => command("candidates", {}, "提示词候选已排队；生成会计入模型预算")}>生成提示词改进候选</button><button disabled={visible.policy.id === "builtin-v2"} onClick={() => command("rollback", {}, "已回滚上一分类版本")}>回滚上一版本</button></div>
      <small>三组对比最多 {(limit + 2) * 3} 次 API 调用（含边界用例），使用统一调用预算与计价。演示模式不会生成虚构指标。</small>
      {visible.candidates.map((c: any) => <details key={c.id}><summary>提示词候选 v{c.body.version} · {names[c.status] || c.status}</summary><p>{c.body.rationale}</p><p>补充指导：{c.body.guidance || "等待模型生成"}</p><small>只增加判断原则，核心权限与执行边界不变。</small>{c.status === "candidate" && <button onClick={() => command("evaluations", { split, limit, candidate_id: c.id }, "候选与当前版本对比已排队")}>与当前版本对比</button>}</details>)}
      {visible.evaluations.map((r: any) => <div className="mail-list-row" key={r.id}><div><strong>{names[r.body.split]} · {r.body.sample_count} 封 · {names[r.status] || r.status}</strong><p>进度 {r.body.cursor}/{r.body.cells.length} · {r.body.verification === "real_api" ? "真实 API 评测" : "尚未真实验证"}</p>{r.body.error && <p>{r.body.error}</p>}</div><div className="actions"><button onClick={() => openReport(r.id)}>查看对比报告</button><button onClick={() => openTrace(r.id)}>查看评测 Trace</button>{["queued", "running"].includes(r.status) && <button onClick={() => command(`evaluations/${r.id}/cancel`, {}, "已取消评测，保留已完成结果")}>取消</button>}{r.status === "completed" && r.body.candidate_id && <button onClick={() => command(`candidates/${r.body.candidate_id}/publish`, { evaluation_id: r.id }, "候选已发布，仅影响后续分析")}>确认发布候选</button>}</div></div>)}
      {report?.scope === account && <div className="mail-perception"><h4>分类对比报告</h4><p>调用失败 {report.body.errors ?? "未完成"} · 边界用例 {report.body.safety_passed == null ? "未完成" : report.body.safety_passed ? "通过" : "未通过"}</p><div className="classification-table"><table><thead><tr><th>方案</th><th>分类准确率</th><th>垃圾判断准确率</th><th>误杀率</th><th>漏拦率</th><th>平均耗时</th></tr></thead><tbody>{Object.entries(report.body.metrics || {}).map(([mode, m]: [string, any]) => <tr key={mode}><td>{names[mode]}</td><td>{percent(m.fields.category.accuracy)}（n={m.fields.category.confirmed}）</td><td>{percent(m.fields.spam_label.accuracy)}（n={m.fields.spam_label.confirmed}）</td><td>{percent(m.false_positive_rate)}</td><td>{percent(m.false_negative_rate)}</td><td>{m.latency_ms == null ? "未知" : `${m.latency_ms} ms`}</td></tr>)}</tbody></table></div><p>无确认标签时显示未测量；不能据此推导整个邮箱的准确率。发布要求真实保留集对比、分类与垃圾判断各至少 10 条标签、正常与垃圾各至少 5 封、边界用例通过且关键指标不退化。</p><div className="actions"><button onClick={() => command(`evaluations/${report.id}/export`, {}, "完整报告已保存到项目 data/mail-evaluations，包含私人邮件内容")}>保存完整报告到本地</button><button onClick={() => openTrace(report.id)}>用量、缓存与费用 Trace</button></div><details><summary>逐例判断与版本</summary><pre>{JSON.stringify({ results: report.body.results, limitations: report.body.limitations }, null, 2)}</pre></details></div>}
    </>}
  </section>;
}
