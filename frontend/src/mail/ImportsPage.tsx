import { useEffect, useState } from "react";
import { useWorkspace } from "./context";
import { pretty, label } from "./shared";
export function ImportsPage() {
  const [analyzeAfterImport, setAnalyzeAfterImport] = useState(false);
  const [analysis, setAnalysis] = useState<any>(null);
  const {
    page,
    accounts,
    account,
    setNotice,
    busy,
    range,
    setRange,
    job,
    setJob,
    imports,
    current,
    act,
    waitJob,
    rangePayload,
    api,
  } = useWorkspace();
  useEffect(() => {
    if (page !== "imports" || !account) {
      setAnalysis(null);
      return;
    }
    let active = true;
    const load = () => void api(`mail/accounts/${account}/perception/summary`)
      .then((value) => { if (active) setAnalysis(value); });
    load();
    const timer = setInterval(load, 3000);
    return () => { active = false; clearInterval(timer); };
  }, [page, account, api]);
  return (
    <>
      {page === "imports" && (
        <>
          <section className="panel">
            <h3>历史导入 · {current?.body.name || "请先选择邮箱"}</h3>
            <p>
              {current?.body.test_account ? "这是本地合成测试邮箱，无远端收取；可在下方分析样本邮件。" : "起止时间按本机时间输入，结束时间不包含在范围内。预览不下载正文、不推进实时游标。"}
            </p>
            <div className="pricing-grid">
              <label>
                开始时间
                <input
                  aria-label="开始时间"
                  type="datetime-local"
                  value={range.start}
                  onChange={(e) =>
                    setRange({ ...range, start: e.target.value })
                  }
                />
              </label>
              <label>
                结束时间
                <input
                  aria-label="结束时间"
                  type="datetime-local"
                  value={range.end}
                  onChange={(e) => setRange({ ...range, end: e.target.value })}
                />
              </label>
              <label>
                本批累计上限
                <input
                  type="number"
                  min={1}
                  max={10000}
                  value={range.limit}
                  onChange={(e) =>
                    setRange({ ...range, limit: +e.target.value })
                  }
                />
              </label>
            </div>
            <label className="mail-import-analysis-option">
              <input type="checkbox" checked={analyzeAfterImport}
                onChange={(e) => setAnalyzeAfterImport(e.target.checked)} />
              导入后分析放行邮件（本批最多 20 封），生成摘要、分类及待办／日程候选
            </label>
            <p>分析会将邮件正文发送至已配置的模型服务，受模型调用预算限制，可能产生费用。未勾选时只入库和索引。</p>
            <div className="actions">
              <button
                disabled={busy || !account || current?.body.test_account}
                onClick={() =>
                  void act(async () => {
                    const result = await waitJob(
                      (await api("mail/imports/preview", rangePayload()))
                        .job_id,
                    );
                    setNotice("匹配邮件：" + result.matched + " 封");
                  }, "预览已完成，查看下方任务结果")
                }
              >
                预览范围
              </button>
              <button
                disabled={busy || !account || current?.body.test_account}
                onClick={() =>
                  void act(
                    () => api("mail/imports", { ...rangePayload(), analyze_after_import: analyzeAfterImport }),
                    analyzeAfterImport ? "导入批次已创建；放行邮件将排队分析" : "导入批次已创建；当前仅入库和索引",
                  )
                }
              >
                创建导入批次
              </button>
              <button
                disabled={!account || current?.body.test_account}
                onClick={() =>
                  void act(
                    async () =>
                      setJob(await api("mail/accounts/" + account + "/status")),
                    "",
                  )
                }
              >
                查看账号状态
              </button>
            </div>
            {job && <pre>{pretty(job)}</pre>}
            {analysis && <div className="mail-analysis-status">
              <h4>当前邮箱的 Agent 处理</h4>
              <p>已入库 {analysis.total} 封 · 已产出 {analysis.ready} 封 · 待分析 {analysis.eligible} 封 · 排队 {analysis.queued} 封 · 分析中 {analysis.running} 封 · 失败 {analysis.failed} 封 · 已过滤 {analysis.filtered} 封</p>
              <div className="actions">
                <button disabled={busy || !analysis.eligible} onClick={() => void act(async () => {
                  const result = await api(`mail/accounts/${account}/perception/batch`, { limit: 20 });
                  setAnalysis(result.summary);
                }, "已将待分析邮件加入队列；稍后到收件箱查看摘要、到待办与跟进确认候选")}>分析待处理邮件（最多 20 封）</button>
                {analysis.failed > 0 && <button disabled={busy} onClick={() => void act(async () => {
                  const result = await api(`mail/accounts/${account}/perception/batch`, { limit: 20, retry_failed: true });
                  setAnalysis(result.summary);
                }, "失败的邮件已重新排队")}>重试失败分析</button>}
              </div>
              <small>“已产出”包含后来被 AI 移入过滤箱的邮件，因此可能与“已过滤”重叠。摘要、分类和感知 Trace 在收件箱或过滤箱；待办／日程候选在“待办与跟进”确认。规则过滤邮件不会自动分析。</small>
            </div>}
            {!current?.body.test_account && <details>
              <summary>离线积压处理</summary>
              <p>
                默认最多补读最近 24 小时的 50
                封。继续将放行当前剩余积压；从现在开始会跳过尚未收取的旧邮件，但不删除远端内容。
              </p>
              <button
                disabled={!account}
                onClick={() =>
                  void act(() =>
                    api(`mail/accounts/${account}/backlog/continue`, {}),
                  )
                }
              >
                继续当前积压
              </button>
              <button
                disabled={!account}
                onClick={() =>
                  void act(() =>
                    api(`mail/accounts/${account}/backlog/from_now`, {}),
                  )
                }
              >
                重新从现在开始
              </button>
            </details>}
          </section>
          {imports.map((r) => (
            <section className="panel" key={r.id}>
              <h3>
                {label[r.status] || r.status} · {r.body.imported}/{r.body.limit}{" "}
                封
              </h3>
              <p>
                {r.body.start} — {r.body.end}
              </p>
              <p>Agent 分析：{r.body.analyze_after_import ? `已选择，已排队 ${r.body.analysis_queued || 0} 封（最多 20 封）` : "未选择，仅入库和索引"}</p>
              {["pause", "resume", "cancel"].map((v, i) => (
                <button
                  key={v}
                  disabled={["completed", "cancelled"].includes(r.status)}
                  onClick={() =>
                    void act(() => api(`mail/imports/${r.id}/${v}`, {}))
                  }
                >
                  {["暂停", "继续", "取消"][i]}
                </button>
              ))}
            </section>
          ))}
        </>
      )}
    </>
  );
}
