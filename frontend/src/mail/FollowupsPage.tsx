import { useEffect, useState } from "react";
import { useWorkspace } from "./context";
import { label, pretty, type Row } from "./shared";

type WorkView = "todos" | "replies" | "review" | "reminders";

export function FollowupsPage() {
  const { page, account, list, busy, setTrace, setDraftForm, act, api, navigate, openMail } = useWorkspace();
  const [digest, setDigest] = useState<any>(null);
  const [view, setView] = useState<WorkView>("todos");
  useEffect(() => {
    if (page !== "followups" || !account) return;
    let active = true;
    void api("mail/digest?account_id=" + encodeURIComponent(account))
      .then((value) => { if (active) setDigest(value); })
      .catch(() => { if (active) setDigest(null); });
    return () => { active = false; };
  }, [page, account, api]);

  const groups: Record<WorkView, Row[]> = {
    todos: list.filter((item) => item.kind === "todo" && ["active", "completed"].includes(item.status)),
    replies: list.filter((item) => item.kind === "mail_followup"),
    review: list.filter((item) => item.status === "candidate" && ["todo", "calendar"].includes(item.kind || "")),
    reminders: list.filter((item) => item.kind === "reminder" && item.status === "active"),
  };
  const tabs: [WorkView, string][] = [
    ["todos", "待办"], ["replies", "邮件跟进"], ["review", "待核实建议"], ["reminders", "提醒"],
  ];
  async function showSource(item: Row) {
    navigate("inbox");
    await openMail(item.body.source_message);
  }
  function itemCard(item: Row) {
    const source = item.body.source_message;
    return <article className="panel" key={item.id}>
      <div className="mail-sectionbar">
        <h3>{item.body.title || item.body.text || "提醒"}</h3>
        <small>{label[item.status] || item.status}</small>
      </div>
      <p>{item.kind === "mail_followup" ? (item.status === "waiting" ? "已回复，等待对方" : "邮件明确要求回复") :
        item.kind === "calendar" ? "日程时间需核实" : item.kind === "reminder" ? "本地提醒" : "本地待办"}
        {item.body.deadline || item.body.due_at || item.body.start ?
          ` · ${item.body.deadline || item.body.due_at || item.body.start}` : ""}</p>
      {item.body.decision?.reason && <small>处理依据：{item.body.decision.reason}</small>}
      {item.body.has_conflict && <p role="alert">与 {item.body.conflicts?.length || 1} 项日程冲突，请核对。</p>}
      {item.body.prior_thread_events?.length > 0 && <p role="alert">这封邮件更新了同一线程的日程时间。确认改期后，旧日程及其提醒会停用。</p>}
      <div className="actions">
        {source && <button onClick={() => void showSource(item)}>查看源邮件</button>}
        {source && <button onClick={() => void act(async () =>
          setTrace(await api("mail/traces/" + source)), "")}>查看来源 Trace</button>}
        {item.kind === "todo" && item.status === "active" &&
          <button disabled={busy} onClick={() => void act(() => api(`mail/todos/${item.id}/complete`, {}), "已完成本地待办")}>完成</button>}
        {item.kind === "todo" && item.status === "completed" &&
          <button disabled={busy} onClick={() => void act(() => api(`mail/todos/${item.id}/reopen`, {}), "待办已重新打开")}>重新打开</button>}
        {item.kind === "mail_followup" && item.status === "active" && source &&
          <button disabled={busy} onClick={() => void act(async () => {
            const draft = await api("mail/drafts", { account_id: item.scope.replace("web:mail:", ""),
              mode: "reply", message_id: source });
            setDraftForm(draft);
            navigate("drafts");
          }, "回复草稿已创建；发送前需要你在草稿页最终确认")}>起草回复</button>}
        {item.kind === "mail_followup" && item.status !== "resolved" &&
          <button disabled={busy} onClick={() => void act(() => api(`mail/followups/${item.id}/resolve`, {}), "邮件跟进已处理")}>标记已处理</button>}
        {item.kind === "mail_followup" && item.status === "resolved" &&
          <button disabled={busy} onClick={() => void act(() => api(`mail/followups/${item.id}/reopen`, {}), "邮件跟进已重新打开")}>重新跟进</button>}
        {item.kind === "reminder" && item.status === "active" && <>
          <button disabled={busy} onClick={() => void act(() => api(`mail/reminders/${item.id}/complete`, { minutes: 30 }), "提醒已完成")}>完成</button>
          <button disabled={busy} onClick={() => void act(() => api(`mail/reminders/${item.id}/snooze`, { minutes: 30 }), "将在 30 分钟后再次提醒")}>稍后 30 分钟</button>
          <button disabled={busy} onClick={() => void act(() => api(`mail/reminders/${item.id}/dismiss`, { minutes: 30 }), "提醒已忽略")}>忽略</button>
        </>}
        {item.status === "candidate" && item.body.source === "perception" && <>
          <button disabled={busy} onClick={() => void act(() => api(`mail/perception/${item.kind === "calendar" ? "calendars" : "todos"}/${item.id}/confirm`, {}), "建议已确认")}>{item.kind === "calendar" && item.body.prior_thread_events?.length ? "确认改期并停用旧日程" : "确认加入"}</button>
          <button disabled={busy} onClick={() => void act(() => api(`mail/perception/${item.kind === "calendar" ? "calendars" : "todos"}/${item.id}/dismiss`, {}), "建议已忽略")}>忽略</button>
        </>}
      </div>
      <details><summary>来源与完整记录</summary><pre>{pretty(item.body)}</pre></details>
    </article>;
  }

  return page === "followups" && <>
    {account && <section className="panel">
      <h3>今日邮件动态</h3>
      <p>{digest?.summary || digest?.message || "尚未生成，可手动生成。"}</p>
      <button disabled={busy} onClick={() => void act(async () => {
        setDigest(await api("mail/digest/generate?account_id=" + encodeURIComponent(account), {}));
      }, "摘要已更新")}>重新生成摘要</button>
    </section>}
    <section className="panel">
      <h3>下一步工作</h3>
      <p>明确且低风险的事项自动进入本地待办或日程；需要回复的邮件单独跟进。仅在证据不足或存在冲突时请你核实。</p>
      <div className="actions" role="tablist" aria-label="工作分类">
        {tabs.map(([key, title]) => <button key={key} role="tab" aria-selected={view === key}
          onClick={() => setView(key)}>{title} {groups[key].length}</button>)}
      </div>
    </section>
    {view === "todos" && <section className="panel">
      <h3>手动添加待办</h3>
      <form onSubmit={(event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        void act(() => api("mail/followups", { account_id: account,
          title: String(data.get("title") || ""),
          deadline: data.get("deadline") ? new Date(String(data.get("deadline"))).toISOString() : null,
        }), "待办已创建");
      }}>
        <label className="mail-field">任务<input name="title" required placeholder="例如：周五前补充实验对比" /></label>
        <label className="mail-field">截止时间<input name="deadline" type="datetime-local" /></label>
        <button className="primary" disabled={!account || busy}>添加待办</button>
      </form>
    </section>}
    {!groups[view].length && <section className="panel empty">{view === "review" ? "没有需要人工核实的建议。" : "此分类暂无事项。"}</section>}
    {groups[view].map(itemCard)}
  </>;
}
