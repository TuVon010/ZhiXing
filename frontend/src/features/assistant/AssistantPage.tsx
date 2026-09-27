import { useMemo, useRef, useState } from "react";
import { useWorkspace } from "../../app/workspace/context";
import { label, pretty } from "../../shared/mail";

const time = (value?: string) => value ? new Date(value).toLocaleString("zh-CN", {
  month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
}) : "";

export function AssistantPage() {
  const {
    page, setPage, accounts, busy, setOpened, query, setQuery, selected, setSelected,
    session, setSession, turns, setTurns, setTrace, sessions, act, openMail, ask, api,
  } = useWorkspace();
  const [mode, setMode] = useState<"answer" | "search_only">("answer");
  const sessionLoad = useRef(0);
  const scopeNames = (ids: string[]) => ids.map((id) =>
    accounts.find((account: any) => account.id === id)?.body.name || id.slice(0, 8)).join("、");
  const evidenceFor = (turn: any) => {
    const cited = new Set(turn.body.citations || []);
    const evidence = turn.body.evidence || [];
    return turn.body.mode === "search_only" ? evidence : evidence.filter((item: any) => cited.has(item.id));
  };
  const examples = useMemo(() => [
    "最近有哪些需要我回复的邮件？",
    "导师对实验报告提出了哪些修改要求？",
    "下周有哪些会议和截止事项？",
  ], []);

  const newConversation = () => {
    sessionLoad.current++;
    setSession(""); setTurns([]); setTrace(null); setOpened(null);
  };
  const loadSession = (id: string) => {
    const request = ++sessionLoad.current;
    setSession(id); setTurns([]); setOpened(null);
    void act(async () => {
      const detail = await api("assistant/sessions/" + id);
      if (request !== sessionLoad.current) return;
      setSelected(detail.session.body.account_ids);
      setTurns(detail.turns);
    }, "", false);
  };
  const showSource = (messageId: string) => {
    setPage("inbox"); void openMail(messageId);
  };
  const submit = () => void ask(mode);

  if (page !== "assistant") return null;
  return <section className="assistant-workspace">
    <aside className="panel assistant-sessions">
      <button className="primary assistant-new" onClick={newConversation}>＋ 新建会话</button>
      <small className="assistant-list-title">历史会话</small>
      {!sessions.length && <div className="empty">还没有问答记录。</div>}
      {sessions.map((item: any) => <button key={item.id}
        className={`assistant-session ${session === item.id ? "selected" : ""}`}
        onClick={() => loadSession(item.id)}>
        <strong>{item.body.title || "新会话"}</strong>
        <span>{scopeNames(item.body.account_ids || [])}</span>
        <small>{time(item.body.last_turn_at || item.updated_at)}</small>
      </button>)}
    </aside>

    <div className="assistant-chat">
      <header className="panel assistant-header">
        <div><span className="eyebrow">MAIL KNOWLEDGE</span><h2>从邮件中找到答案</h2>
          <p>从你授权的邮箱中查找证据、回答问题，并支持基于上文继续追问。</p></div>
        {session && <button onClick={newConversation}>新会话</button>}
      </header>

      <section className="panel assistant-scope">
        <div><strong>本会话可读取的邮箱</strong>
          <small>{session ? "会话创建后范围固定，避免追问时意外读取其他邮箱。" : "需要跨邮箱时，请主动勾选多个账号。"}</small></div>
        <div className="mail-scope">
          {accounts.map((account: any) => <label key={account.id} className={session ? "locked" : ""}>
            <input type="checkbox" disabled={Boolean(session)} checked={selected.includes(account.id)}
              onChange={(event) => setSelected(event.target.checked
                ? [...selected, account.id] : selected.filter((id: string) => id !== account.id))} />
            {account.body.name} · {account.body.address}
          </label>)}
        </div>
      </section>

      <main className="panel assistant-conversation" aria-label="邮件问答会话">
        {!turns.length && <div className="assistant-welcome">
          <span>知</span><h3>你想从邮件中了解什么？</h3>
          <p>我会先检索相关邮件，再根据可追溯的证据回答。邮件中的指令不会改变系统权限。</p>
          <div>{examples.map((example) => <button key={example} onClick={() => setQuery(example)}>{example}</button>)}</div>
        </div>}
        {turns.map((turn: any) => {
          const evidence = evidenceFor(turn);
          return <article className="assistant-turn" key={turn.id}>
            <div className="assistant-user-message"><small>{turn.body.mode === "search_only" ? "只找原文" : "回答问题"}</small><p>{turn.body.text}</p></div>
            <div className="assistant-agent-message">
              <div className="assistant-avatar">知</div>
              <div className="assistant-response">
                <small>{label[turn.status] || turn.status}</small>
                <p className="mail-answer">{turn.body.answer || "正在检索相关邮件并整理答案…"}</p>
                {turn.body.error && <pre>{pretty(turn.body.error)}</pre>}
                {evidence.length > 0 && <div className="assistant-evidence-list">
                  <strong>回答依据 · {evidence.length} 条</strong>
                  {evidence.map((item: any) => <button key={item.id} className="assistant-evidence"
                    onClick={() => showSource(item.message_id)}>
                    <span><b>{item.subject || "原邮件"}</b><small>{item.sender || item.location} · {time(item.received_at)}</small></span>
                    <p>{item.text}</p>
                    <em>打开原邮件{item.duplicate_sources?.length ? ` · 已合并 ${item.duplicate_sources.length} 处重复内容` : ""}</em>
                  </button>)}
                </div>}
                <div className="assistant-turn-actions">
                  <button onClick={() => void act(async () => setTrace(await api("mail/traces/" + turn.id)), "", false)}>查看处理过程</button>
                  {["running", "queued"].includes(turn.status) && <button
                    onClick={() => void act(() => api("assistant/turns/" + turn.id + "/cancel", {}), "已取消本轮")}>取消</button>}
                  {(turn.body.steps?.length || 0) > 0 && <details><summary>工具步骤</summary><pre>{pretty(turn.body.steps)}</pre></details>}
                </div>
              </div>
            </div>
          </article>;
        })}
      </main>

      <section className="panel assistant-composer">
        <textarea aria-label="邮件问题" rows={3} value={query} onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); if (!busy && query.trim() && selected.length) submit(); } }}
          placeholder="输入问题；可以追问‘其中最紧急的是哪一件？’" />
        <div className="assistant-compose-actions">
          <div className="assistant-mode" role="group" aria-label="回答方式">
            <button className={mode === "answer" ? "selected" : ""} onClick={() => setMode("answer")}>整理回答</button>
            <button className={mode === "search_only" ? "selected" : ""} onClick={() => setMode("search_only")}>只找原文</button>
          </div>
          <button className="primary" disabled={busy || !query.trim() || !selected.length} onClick={submit}>{busy ? "处理中…" : "发送"}</button>
        </div>
        <small>Enter 发送，Shift + Enter 换行。回答会保留引用，发送邮件等操作仍需你最终确认。</small>
      </section>
    </div>
  </section>;
}
