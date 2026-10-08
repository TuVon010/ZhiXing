import { useEffect, useMemo, useState } from "react";
import { useWorkspace } from "../../app/workspace/context";
import { pretty, label, type Row } from "../../shared/mail";

const MAILBOXES = ["inbox", "archive", "filter", "trash"];

export function MailboxPage() {
  const {
    page, setPage, list, offset, setOffset, opened, setOpened, setTrace,
    thread, act, openMail, makeDraft, api, busy,
    inboxSort, setInboxSort, inboxView, setInboxView,
    inboxCategory, setInboxCategory,
  } = useWorkspace();
  const [threadInsight, setThreadInsight] = useState<any>(null);
  const [threadBusy, setThreadBusy] = useState(false);

  useEffect(() => {
    if (!opened?.id || !MAILBOXES.includes(page)) return;
    let active = true;
    const timer = setInterval(() => {
      void api(`mail/messages/${opened.id}`).then((value) => {
        if (active) setOpened(value);
      }).catch(() => {});
    }, 3000);
    return () => { active = false; clearInterval(timer); };
  }, [opened?.id, page, api, setOpened]);

  useEffect(() => setThreadInsight(null), [opened?.id]);

  async function feedback(change: Record<string, unknown>, message: string) {
    if (!opened) return;
    await act(async () => {
      await api(`mail/messages/${opened.id}/perception/feedback`, { ...change, message_id: opened.id });
      setOpened(await api(`mail/messages/${opened.id}`));
    }, message);
  }

  async function moveTo(status: string, message: string) {
    if (!opened) return;
    await act(async () => {
      await api(`mail/messages/${opened.id}/state`, { status });
      setOpened(null);
    }, message);
  }

  async function trash() {
    if (!opened) return;
    await act(async () => {
      await api(`mail/records/${opened.id}/trash`, {});
      setOpened(null);
    }, "已移入本地回收站；原邮箱中的邮件不受影响");
  }

  async function restore() {
    if (!opened) return;
    await act(async () => {
      await api(`mail/records/${opened.id}/restore`, {});
      setOpened(null);
    }, "已恢复到原来的本地邮箱");
  }

  async function summarizeThread() {
    if (!opened || threadBusy) return;
    setThreadBusy(true);
    setThreadInsight(null);
    try {
      await act(async () => {
        const session = await api("assistant/sessions", {
          account_ids: [opened.body.account_id], thread_id: opened.body.thread_id,
        });
        const created = await api(`assistant/sessions/${session.id}/turns`, {
          text: "总结这组邮件往来的最终结论、发生的变更、尚未完成事项和明确截止时间；每条结论引用证据。",
        });
        setThreadInsight({ turn: created });
        for (let i = 0; i < 240; i += 1) {
          const detail = await api(`assistant/turns/${created.id}`);
          setThreadInsight(detail);
          if (!["queued", "running"].includes(detail.turn.status)) return;
          await new Promise((resolve) => setTimeout(resolve, 500));
        }
      }, "线程结论已整理", false);
    } finally {
      setThreadBusy(false);
    }
  }

  if (!MAILBOXES.includes(page)) return null;
  const descriptions: Record<string, string> = {
    inbox: "只显示仍需处理的邮件；完成后可归档，需要时仍能检索。",
    archive: "已经处理完成的邮件。归档仅影响知行本地状态，原邮箱不变。",
    filter: "集中查看待复核与疑似垃圾邮件；这些邮件不会进入检索和自动处理。",
    trash: "本地移除的邮件会保留在这里，可恢复到移除前的位置。原邮箱不变。",
  };

  return <>
    <div className="mail-sectionbar"><p>{descriptions[page]}</p>{page === "inbox" && <button onClick={() => void makeDraft()}>新邮件草稿</button>}</div>
    {page === "inbox" && <section className="mail-filterbar">
      <select aria-label="收件箱范围" value={inboxView} onChange={(e) => setInboxView(e.target.value)}>
        <option value="all">全部邮件</option><option value="today">今天</option><option value="unread">未读</option><option value="actionable">需要行动</option><option value="pending">待 Agent 分析</option>
      </select>
      <select aria-label="邮件分类" value={inboxCategory} onChange={(e) => setInboxCategory(e.target.value)}>
        <option value="">全部分类</option><option value="work">工作</option><option value="personal">个人</option><option value="notification">系统通知</option><option value="ad">广告</option><option value="other">其他</option>
      </select>
      <select aria-label="邮件排序" value={inboxSort} onChange={(e) => setInboxSort(e.target.value)}><option value="smart">智能排序</option><option value="latest">最新优先</option><option value="oldest">最早优先</option></select>
    </section>}
    <div className="mail-columns">
      <section className="panel mail-list">
        {!list.length && <div className="empty">{emptyText(page)}</div>}
        {list.map((message: Row) => <MailRow key={message.id} message={message} current={opened?.id === message.id} openMail={openMail} />)}
        <div className="actions"><button disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 30))}>上一页</button><button disabled={list.length < 30} onClick={() => setOffset(offset + 30)}>下一页</button></div>
      </section>
      <section className="panel mail-reader">
        {opened ? <>
          <small>{opened.body.sender} → {opened.body.to?.join(", ")}</small><h2>{opened.body.subject}</h2>
          {opened.body.incomplete && <p className="mail-warning">历史资料缺少完整原始头部，收件时间可能为旧入库时间。</p>}
          <MailboxActions opened={opened} page={page} thread={thread} busy={busy || threadBusy} onDraft={makeDraft} onMove={moveTo} onTrash={trash} onRestore={restore} onSummarize={summarizeThread}
            onAnalyze={() => act(() => api(`mail/messages/${opened.id}/perception`, {}), "已加入分析队列；完成后本页会显示结果")}
            onUnread={() => act(async () => { await api(`mail/messages/${opened.id}/read`, { read: false }); setOpened({ ...opened, body: { ...opened.body, read_at: undefined } }); }, "已标记为未读")} go={setPage} />
          {!opened.body.perception && !["filtered", "review", "trashed"].includes(opened.status) && <section className="mail-agent-empty"><strong>等待 Agent 分析</strong><p>分析后会生成摘要、优先级、待办、日程和回复判断。</p><button className="primary" onClick={() => void act(() => api(`mail/messages/${opened.id}/perception`, {}), "已加入分析队列；完成后本页会显示结果")}>立即分析</button></section>}
          {threadInsight && <ThreadInsight value={threadInsight} api={api} openMail={openMail} setTrace={setTrace} act={act} />}
          {opened.body.perception && <PerceptionPanel opened={opened} feedback={feedback} analyze={() => act(() => api(`mail/messages/${opened.id}/perception`, {}), "已重新加入分析队列")} trace={() => act(async () => setTrace(await api(`mail/traces/${opened.id}`)), "")} />}
          <pre className="mail-body">{opened.body.raw_text || opened.body.text}</pre>
          {opened.body.attachments?.map((attachment: any) => <details key={attachment.id}><summary>附件：{attachment.name} · {attachment.status}</summary>{attachment.segments?.map((segment: any, index: number) => <p key={index}>{segment.location}：{segment.text}</p>)}</details>)}
          <details><summary>过滤依据与原始头部</summary><pre>{pretty({ filter: opened.body.filter, headers: opened.body.headers })}</pre></details>
          <h3>同一线程 · {thread?.messages.length || 0} 封</h3>
          {thread?.messages.map((row: Row) => <button key={row.id} onClick={() => void openMail(row.id)}>{row.body.subject} · {new Date(row.body.received_at).toLocaleString()}</button>)}
          <details><summary>手工关联到线程</summary><form onSubmit={(event) => { event.preventDefault(); const target = new FormData(event.currentTarget).get("target"); void act(() => api(`mail/messages/${opened.id}/thread`, { target_thread_id: target }), "已关联"); }}><input name="target" placeholder="目标线程 ID" required /><button>关联</button></form><small>当前线程：{opened.body.thread_id}</small></details>
        </> : <div className="empty">选择邮件查看正文、Agent 结果和往来线程。</div>}
      </section>
    </div>
  </>;
}

function MailRow({ message, current, openMail }: { message: Row; current: boolean; openMail: (id: string) => Promise<void> }) {
  return <button className={`mail-row ${current ? "current" : ""}`} onClick={() => void openMail(message.id)}>
    <span className="mail-row-meta">{message.body.sender_display || message.body.sender || "历史来源"}<small>{!message.body.read_at ? "未读 · " : ""}{label[message.status] || message.status}</small></span>
    <strong>{message.body.subject || "无主题"}</strong>
    {!message.body.perception && ["active", "review"].includes(message.status) && <small>待 Agent 分析</small>}
    <span>{message.body.perception?.summary || message.body.text?.slice(0, 90)}{message.body.perception?.priority === "high" && <span className="mail-tag mail-tag-high">高优先</span>}{message.body.perception?.category === "ad" && <span className="mail-tag mail-tag-ad">广告</span>}{message.body.perception?.needs_reply && <span className="mail-tag mail-tag-reply">待回复</span>}</span>
    <small>{new Date(message.body.received_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })} · {message.body.index_status}</small>
  </button>;
}

function MailboxActions({ opened, page, thread, busy, onDraft, onMove, onTrash, onRestore, onSummarize, onAnalyze, onUnread, go }: any) {
  const perception = opened.body.perception || {};
  const hasThreadContext = (thread?.messages?.length || 0) > 1 || (opened.body.attachments?.length || 0) > 0;
  const hasCalendar = perception.calendar_events?.length > 0;
  const hasTodos = perception.todos?.length > 0;
  if (opened.status === "trashed" || page === "trash") return <div className="mail-state-banner"><div><strong>这封邮件在本地回收站</strong><small>恢复后会回到移除前的位置。</small></div><button className="primary" onClick={() => void onRestore()}>恢复邮件</button></div>;
  if (["review", "filtered"].includes(opened.status)) return <div className={`mail-state-banner ${opened.status === "filtered" ? "danger" : "warning"}`}><div><strong>{opened.status === "review" ? "需要你确认是否放行" : "已停止自动处理"}</strong><small>{opened.status === "review" ? "尚未进入搜索、待办和日程处理。" : "疑似垃圾邮件不会进入搜索和 Agent 上下文。"}</small></div><div className="actions"><button className="primary" onClick={() => void onMove("active", "已放行到收件箱并排队建立索引")}>放行到收件箱</button>{opened.status === "review" && <button onClick={() => void onMove("filtered", "已确认为垃圾邮件")}>确认垃圾</button>}<button onClick={() => void onTrash()}>移入回收站</button></div></div>;
  return <div className="mail-detail-actions"><div className="actions">
    {opened.status === "archived" ? <button className="primary" onClick={() => void onMove("active", "已恢复到收件箱")}>恢复到收件箱</button> : perception.needs_reply ? <button className="primary" onClick={() => void onDraft("reply", true)}>AI 起草回复</button> : hasCalendar ? <button className="primary" onClick={() => go("calendar")}>查看提取的日程</button> : hasTodos ? <button className="primary" onClick={() => go("followups")}>查看提取的待办</button> : <button className="primary" onClick={() => void onMove("archived", "已完成处理并移入归档箱")}>完成处理</button>}
    <button onClick={() => void onDraft("reply")}>回复</button>
    {!perception.needs_reply && <button onClick={() => void onDraft("reply", true)}>AI 起草回复</button>}
    {(opened.body.cc?.length > 0 || opened.body.to?.length > 1) && <button onClick={() => void onDraft("reply_all")}>回复全部</button>}
    {hasThreadContext && <button disabled={busy} onClick={() => void onSummarize()}>{busy ? "正在总结…" : "总结整段往来"}</button>}
    <details className="mail-more-actions"><summary>更多</summary><div><button onClick={() => void onAnalyze()}>{perception.summary ? "重新分析" : "分析邮件"}</button><button onClick={() => void onUnread()}>标记未读</button>{opened.status === "active" && <button onClick={() => void onMove("archived", "已完成处理并移入归档箱")}>归档</button>}<button onClick={() => void onTrash()}>移入回收站</button></div></details>
  </div></div>;
}

function ThreadInsight({ value, api, openMail, setTrace, act }: any) {
  const turn = value.turn;
  return <section className="mail-thread-insight"><div className="mail-sectionbar"><div><small>线程 Agent</small><h3>整段往来结论</h3></div><span>{label[turn.status] || turn.status}</span></div><p className="mail-answer">{turn.body.answer || "正在展开线程、检索证据并整理结论…"}</p>{turn.body.citations?.map((id: string) => { const evidence = turn.body.evidence?.find((item: any) => item.id === id); return evidence ? <button key={id} onClick={() => void openMail(evidence.message_id)}>查看证据 · {evidence.location}</button> : null; })}<button onClick={() => void act(async () => setTrace(await api(`mail/traces/${turn.id}`)), "")}>查看本次 Agent Trace</button></section>;
}

function PerceptionPanel({ opened, feedback, analyze, trace }: any) {
  const perception = opened.body.perception;
  const [category, setCategory] = useState(perception.category);
  const [priority, setPriority] = useState(perception.priority);
  const [needsReply, setNeedsReply] = useState(String(Boolean(perception.needs_reply)));
  const [spam, setSpam] = useState(opened.body.perception_overrides?.spam_label || "unconfirmed");
  const [remember, setRemember] = useState(false);
  const [note, setNote] = useState("");
  useEffect(() => { setCategory(perception.category); setPriority(perception.priority); setNeedsReply(String(Boolean(perception.needs_reply))); setSpam(opened.body.perception_overrides?.spam_label || "unconfirmed"); setRemember(false); setNote(""); }, [opened.id, perception.category, perception.priority, perception.needs_reply, perception.spam_score, opened.body.classification_label_revision]);
  const changes = useMemo(() => {
    const result: Record<string, unknown> = {};
    if (category !== perception.category) result.category = category;
    if (priority !== perception.priority) result.priority = priority;
    if ((needsReply === "true") !== Boolean(perception.needs_reply)) result.needs_reply = needsReply === "true";
    if (spam !== "unconfirmed" && spam !== opened.body.perception_overrides?.spam_label) result.spam_label = spam;
    return result;
  }, [category, priority, needsReply, spam, perception, opened.body.perception_overrides]);
  const changed = Object.keys(changes).length > 0;
  return <div className="mail-perception">
    <div className="mail-sectionbar"><div><h3>AI 感知结果</h3>{Object.keys(opened.body.perception_overrides || {}).length > 0 && <small>已应用人工纠正，重新分析不会覆盖</small>}</div><div className="actions"><button onClick={() => void trace()}>查看 Trace</button><button onClick={() => void analyze()}>重新分析</button></div></div>
    <div className="mail-perception-grid"><div><strong>摘要</strong><p>{perception.summary || "（无）"}</p></div><div><strong>分类</strong><p>{categoryLabel(perception.category)}</p></div><div><strong>优先级</strong><p>{priorityLabel(perception.priority)}</p></div><div><strong>垃圾评分</strong><p>{(perception.spam_score * 100).toFixed(0)}%</p></div><div><strong>需要回复</strong><p>{perception.needs_reply ? "是" : "否"}</p></div><div><strong>置信度</strong><p>{(perception.confidence * 100).toFixed(0)}%</p></div></div>
    <small>评分与置信度是 AI 的参考判断，未经概率校准；广告或订阅不等于垃圾邮件。</small>
    {perception.todos?.length > 0 && <div><strong>提取的待办</strong><ul>{perception.todos.map((todo: any, index: number) => <li key={index}>{todo.action}{todo.deadline && ` · 截止: ${new Date(todo.deadline).toLocaleString("zh-CN")}`}</li>)}</ul></div>}
    {perception.calendar_events?.length > 0 && <div><strong>提取的日程</strong><ul>{perception.calendar_events.map((event: any, index: number) => <li key={index}>{event.title}{event.start && ` · ${new Date(event.start).toLocaleString("zh-CN")}`}{event.location && ` · ${event.location}`}</li>)}</ul></div>}
    {perception.reasons?.length > 0 && <div className="mail-perception-reasons"><strong>判断理由</strong><ul>{perception.reasons.map((reason: string, index: number) => <li key={index}>{reason}</li>)}</ul></div>}
    <details><summary>调整 AI 判断</summary><form className="mail-correction" onSubmit={(event) => { event.preventDefault(); void feedback({ ...changes, remember, note, expected_revision: opened.body.classification_label_revision || 0 }, remember ? "已保存确认标注，并生成待确认的记忆候选" : "已保存本封邮件纠正与确认标注"); }}>
      <div className="mail-correction-grid"><label>分类<select value={category} onChange={(event) => setCategory(event.target.value)}><option value="work">工作</option><option value="personal">个人</option><option value="notification">系统通知</option><option value="ad">广告/营销</option><option value="other">其他</option></select></label><label>优先级<select value={priority} onChange={(event) => setPriority(event.target.value)}><option value="high">高</option><option value="normal">普通</option><option value="low">低</option></select></label><label>是否需要回复<select value={needsReply} onChange={(event) => setNeedsReply(event.target.value)}><option value="true">需要回复</option><option value="false">无需回复</option></select></label><label>邮件性质<select value={spam} onChange={(event) => setSpam(event.target.value)}><option value="unconfirmed">尚未人工确认</option><option value="normal">正常邮件</option><option value="spam">垃圾邮件</option><option value="uncertain">不能确定，待复核</option></select></label></div>
      <textarea rows={2} value={note} onChange={(event) => setNote(event.target.value)} placeholder="可选：说明判断依据；勾选长期偏好时，可说明今后的适用范围" />
      <label className="mail-memory-choice"><input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} />也把这次纠正提炼为长期偏好候选</label><p>仅确认的字段进入标注集，可供后续相似邮件参考；广告不等于垃圾。{remember ? "长期偏好候选仍需在“记忆”页确认。" : "不会自动形成发件人白名单或修改模型参数。"}</p><div className="actions"><button className="primary" disabled={!changed}>保存调整</button><button type="button" onClick={() => void feedback({ category, priority, needs_reply: needsReply === "true", ...(spam !== "unconfirmed" ? { spam_label: spam } : {}), note, remember, expected_revision: opened.body.classification_label_revision || 0 }, "已确认所选判断，保存为抽查标注")}>确认所选判断（抽查）</button></div>
    </form></details>
  </div>;
}

function emptyText(page: string) { return ({ inbox: "收件箱已处理完。", archive: "暂无归档邮件。", filter: "暂无待复核或疑似垃圾邮件。", trash: "回收站为空。" } as Record<string, string>)[page]; }
function categoryLabel(value: string) { return ({ work: "工作", personal: "个人", ad: "广告/营销", notification: "系统通知", other: "其他" } as Record<string, string>)[value] || value; }
function priorityLabel(value: string) { return ({ high: "高", normal: "普通", low: "低" } as Record<string, string>)[value] || value; }
