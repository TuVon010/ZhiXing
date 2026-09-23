import { useEffect, useState } from "react";
import { useWorkspace } from "./context";
import { pretty, label } from "./shared";
export function FollowupsPage() {
  const { page, account, list, busy, setTrace, act, api } = useWorkspace();
  const [digest, setDigest] = useState<any>(null);
  useEffect(() => {
    if (page !== "followups" || !account) return;
    let active = true;
    void api("mail/digest?account_id=" + encodeURIComponent(account))
      .then((value) => { if (active) setDigest(value); })
      .catch(() => { if (active) setDigest(null); });
    return () => { active = false; };
  }, [page, account, api]);
  return (
    <>
      {page === "followups" && (
        <>
          {account && <section className="panel">
            <h3>昨日邮件摘要</h3>
            <p>{digest?.summary || digest?.message || "尚未生成，可手动生成。"}</p>
            <button disabled={busy} onClick={() => void act(async () => {
              const value = await api("mail/digest/generate?account_id=" + encodeURIComponent(account), {});
              setDigest(value);
            }, "摘要已更新")}>重新生成摘要</button>
            {digest?.needs_reply?.length > 0 && <p>待回复：{digest.needs_reply.map((m: any) => m.subject).join("、")}</p>}
          </section>}
          <section className="panel">
            <h3>待办与跟进</h3>
            <p>待办按邮箱隔离。这里只保存本地任务，不会修改邮箱服务器。</p>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                const d = new FormData(e.currentTarget);
                void act(
                  () =>
                    api("mail/followups", {
                      account_id: account,
                      title: String(d.get("title") || ""),
                      deadline: d.get("deadline")
                        ? new Date(String(d.get("deadline"))).toISOString()
                        : null,
                    }),
                  "待办已创建",
                );
              }}
            >
              <label className="mail-field">
                任务
                <input
                  name="title"
                  required
                  placeholder="例如：周五前补充实验对比"
                />
              </label>
              <label className="mail-field">
                截止时间
                <input name="deadline" type="datetime-local" />
              </label>
              <button className="primary" disabled={!account || busy}>
                添加待办
              </button>
              {!account && <small>请先在顶部选择邮箱账号。</small>}
            </form>
          </section>
          {list.map((t) => (
            <article className="panel" key={t.id}>
              <h3>{t.body.title || t.body.text || "提醒"}</h3>
              <p>
                {
                  { todo: "待办", calendar: "日程", reminder: "提醒" }[
                    t.kind || "todo"
                  ]
                }{" "}
                ·{" "}
                {t.body.deadline ||
                  t.body.due_at ||
                  t.body.start ||
                  "未设置时间"}{" "}
                · {label[t.status] || t.status} · {t.scope}
              </p>
              <div className="actions">
                {t.status === "candidate" && t.body.source === "perception" && (
                  <>
                    <button disabled={busy} onClick={() => void act(() => api(`mail/perception/${t.kind === "calendar" ? "calendars" : "todos"}/${t.id}/confirm`, {}), "候选已确认")}>确认加入</button>
                    <button disabled={busy} onClick={() => void act(() => api(`mail/perception/${t.kind === "calendar" ? "calendars" : "todos"}/${t.id}/dismiss`, {}), "候选已忽略")}>忽略</button>
                  </>
                )}
                {t.body.run_id && (
                  <button
                    onClick={() =>
                      void act(
                        async () =>
                          setTrace(await api("mail/traces/" + t.body.run_id)),
                        "",
                      )
                    }
                  >
                    查看来源 Trace
                  </button>
                )}
                {t.kind === "todo" && t.status === "active" && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      void act(
                        () =>
                          api("actions", {
                            tool: "update_todo",
                            args: { id: t.id, status: "completed" },
                          }),
                        "已提交完成申请，请在审批中心确认",
                      )
                    }
                  >
                    申请标记完成
                  </button>
                )}
              </div>
              {t.body.has_conflict && <p role="alert">与 {t.body.conflicts?.length || 1} 项日程时间冲突，请核对后确认。</p>}
              <details>
                <summary>来源与记录</summary>
                <pre>{pretty(t.body)}</pre>
              </details>
            </article>
          ))}
        </>
      )}
    </>
  );
}
