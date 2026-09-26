import { useWorkspace } from "../../app/workspace/context";

const localTime = (value?: string) => value ? new Date(value).toLocaleString("zh-CN", {
  month: "numeric", day: "numeric", weekday: "short", hour: "2-digit", minute: "2-digit",
}) : "时间待确认";

const relativeSync = (value?: string) => {
  if (!value) return "尚未检查";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return `${seconds} 秒前检查`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前检查`;
  return `${Math.floor(seconds / 3600)} 小时前检查`;
};

export function HomePage() {
  const { page, home, accounts, selected, setSelected, query, setQuery, turns, busy,
    ask, navigate, openMail, act, api } = useWorkspace();
  if (page !== "home") return null;
  const latest = turns[0];
  const hour = new Date().getHours();
  const greeting = hour < 11 ? "早上好" : hour < 14 ? "中午好" : hour < 19 ? "下午好" : "晚上好";
  const status = home?.account_status || [];
  const enabled = status.filter((item: any) => item.enabled);
  const lastChecked = enabled.map((x: any) => x.last_checked_at).filter(Boolean).sort().at(-1);
  const syncText = !status.length ? "尚未添加邮箱" : enabled.length
    ? `${enabled.length} 个邮箱正在守护 · ${relativeSync(lastChecked)}`
    : "邮件自动收取已暂停";
  const showMail = async (id: string) => { navigate("inbox"); await openMail(id); };
  const next = home?.brief?.next_event;

  return <>
    <section className="home-hero">
      <div>
        <span className="eyebrow">{new Date().toLocaleDateString("zh-CN", { month: "long", day: "numeric", weekday: "long" })}</span>
        <h2>{greeting}，{home?.brief?.headline || "今天从重要的事情开始"}</h2>
        <p>{home?.brief?.detail || "知行会从邮件中整理待办、日程和需要回复的事项。"}</p>
      </div>
      <button className={`home-guard ${enabled.length ? "active" : "paused"}`} onClick={() => navigate("accounts")}>
        <span>{enabled.length ? "●" : "○"}</span><span>{syncText}<small>{status.reduce((sum: number, x: any) => sum + (x.pending_jobs || 0), 0)} 个后台任务</small></span>
      </button>
    </section>

    <div className="home-stats">
      {[
        ["今日新邮件", home?.stats?.new || 0, `${home?.stats?.analyzed || 0} 封已分析`],
        ["需要行动", home?.focus?.length || 0, `${home?.stats?.overdue || 0} 项已逾期`],
        ["待回复", home?.stats?.reply || 0, "来自邮件判断"],
        ["待分析", home?.stats?.pending_analysis || 0, `${home?.stats?.filtered || 0} 封已过滤`],
      ].map(([name, value, note]) => <section className="panel" key={String(name)}>
        <small>{name}</small><strong>{value}</strong><span>{note}</span>
      </section>)}
    </div>

    <div className="home-grid home-primary-grid">
      <section className="panel home-focus">
        <div className="mail-sectionbar"><div><span className="eyebrow">FOCUS</span><h3>下一步行动</h3></div><button onClick={() => navigate("followups")}>查看全部</button></div>
        {!(home?.focus?.length) && <div className="empty">目前没有需要处理的事项。</div>}
        {(home?.focus || []).map((item: any) => <article className={`home-item urgency-${item.urgency}`} key={item.id}>
          <span className="home-check" aria-hidden="true">{item.kind === "todo" ? "○" : item.status === "waiting" ? "↗" : "↩"}</span>
          <div><strong>{item.body.title || "待处理事项"}</strong><small>
            {item.urgency === "overdue" ? "已逾期 · " : item.urgency === "today" ? "今天截止 · " : item.urgency === "waiting" ? "等待对方回复 · " : ""}
            {item.body.deadline ? localTime(item.body.deadline) : item.body.reason || (item.kind === "mail_followup" ? "需要回复" : "")}
          </small></div>
          <div className="home-item-actions">
            {item.body.source_message && <button onClick={() => void showMail(item.body.source_message)}>邮件</button>}
            {item.kind === "todo" && <button onClick={() => void act(() => api(`mail/todos/${item.id}/complete`, {}), "待办已完成")}>完成</button>}
          </div>
        </article>)}
        {(home?.review?.length || 0) > 0 && <button className="home-suggestion" onClick={() => navigate("followups")}>
          Agent 还有 {home.review.length} 条低置信度建议，等待你核实
        </button>}
      </section>

      <section className="panel home-agenda">
        <div className="mail-sectionbar"><div><span className="eyebrow">AGENDA</span><h3>今日日程</h3></div><button onClick={() => navigate("calendar")}>打开日历</button></div>
        {next && <div className="next-event"><small>下一项</small><strong>{next.body.title}</strong><span>{localTime(next.body.start)}{next.body.location ? ` · ${next.body.location}` : ""}</span></div>}
        {!(home?.today_calendar?.length) && <div className="empty">今天没有固定日程。</div>}
        <div className="agenda-list">
          {(home?.today_calendar || []).map((item: any) => <article className={`agenda-item ${item.is_next ? "future" : "past"}`} key={item.id}>
            <time>{new Date(item.body.start).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</time>
            <span></span><div><strong>{item.body.title}</strong><small>{item.body.location || (item.body.source_message ? "来自邮件" : "本地日程")}</small></div>
          </article>)}
        </div>
        {(home?.upcoming_calendar?.length || 0) > 0 && <details className="upcoming-events"><summary>未来七天 · {home.upcoming_calendar.length} 项</summary>
          {home.upcoming_calendar.map((item: any) => <button key={item.id} onClick={() => navigate("calendar")}><span>{localTime(item.body.start)}</span><strong>{item.body.title}</strong></button>)}
        </details>}
      </section>
    </div>

    <section className="panel home-activity">
      <div className="mail-sectionbar"><div><span className="eyebrow">AGENT ACTIVITY</span><h3>知行刚刚处理</h3></div><button onClick={() => navigate("runs")}>查看运行记录</button></div>
      {!(home?.activity?.length) && <div className="empty">今天还没有新的 Agent 分析结果。</div>}
      <div className="activity-strip">
        {(home?.activity || []).map((item: any) => <button key={item.message_id} onClick={() => void showMail(item.message_id)}>
          <span>已分析</span><strong>{item.title}</strong><small>{item.summary || "已完成分类和行动识别"}</small>
          <em>{item.outcomes.todos ? `待办 ${item.outcomes.todos}` : ""}{item.outcomes.calendar ? ` · 日程 ${item.outcomes.calendar}` : ""}{item.outcomes.needs_reply ? " · 需要回复" : ""}</em>
        </button>)}
      </div>
    </section>

    <section className="panel home-important">
      <div className="mail-sectionbar"><div><span className="eyebrow">ATTENTION</span><h3>最近需要关注的重要邮件</h3></div><button onClick={() => navigate("inbox")}>进入收件箱</button></div>
      {!(home?.important?.length) && <div className="empty">最近七天没有需要优先关注的邮件。</div>}
      {(home?.important || []).map((mail: any) => <button className="home-mail" key={mail.id} onClick={() => void showMail(mail.id)}>
        <span><strong>{mail.body.subject || "无主题"}</strong><small>{mail.body.sender_display || mail.body.sender} · {localTime(mail.body.received_at)}</small></span>
        <span>{mail.body.perception?.summary || mail.body.text?.slice(0, 80)}</span>
      </button>)}
    </section>

    <section className="panel home-agent">
      <div><span className="eyebrow">ASK ZHIXING</span><h3>从邮件和记忆中继续追问</h3></div>
      <div className="mail-scope">
        {accounts.filter((a: any) => !a.body.test_account).map((a: any) => <label key={a.id}>
          <input type="checkbox" checked={selected.includes(a.id)} onChange={(e) =>
            setSelected(e.target.checked ? [...selected, a.id] : selected.filter((id: string) => id !== a.id))} />{a.body.name}
        </label>)}
      </div>
      <div className="home-ask-row"><input aria-label="首页 Agent 问题" value={query} onChange={(e) => setQuery(e.target.value)}
        placeholder="例如：导师最近对实验结果有什么要求？" />
        <button className="primary" disabled={busy || !query.trim() || !selected.length} onClick={() => void ask()}>询问</button></div>
      {latest?.body?.answer && <div className="home-answer"><strong>Agent</strong><p>{latest.body.answer}</p><button onClick={() => navigate("assistant")}>进入完整会话</button></div>}
    </section>
  </>;
}
