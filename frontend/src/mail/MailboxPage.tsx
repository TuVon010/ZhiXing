import { useWorkspace } from "./context";
import { pretty, label, type Row } from "./shared";
export function MailboxPage() {
  const {
    page,
    setPage,
    list,
    offset,
    setOffset,
    opened,
    thread,
    setQuery,
    setSelected,
    setSession,
    setTurns,
    current,
    act,
    openMail,
    makeDraft,
    api,
  } = useWorkspace();
  return (
    <>
      {["inbox", "filter"].includes(page) && (
        <>
          <div className="mail-sectionbar">
            <p>
              {page === "filter"
                ? "过滤只影响本地处理；可恢复，不会删除原邮箱邮件。"
                : "邮件收取、索引、Agent 分析相互独立。"}{" "}
            </p>
            <button onClick={() => void makeDraft()}>新邮件草稿</button>
          </div>
          <div className="mail-columns">
            <section className="panel mail-list">
              {!list.length && (
                <div className="empty">暂无邮件，请添加账号或调整筛选。</div>
              )}
              {list.map((m) => (
                <button
                  className={
                    "mail-row " + (opened?.id === m.id ? "current" : "")
                  }
                  key={m.id}
                  onClick={() => void openMail(m.id)}
                >
                  <span className="mail-row-meta">
                    {m.body.sender_display || m.body.sender || "历史来源"}{" "}
                    <small>{label[m.status] || m.status}</small>
                  </span>
                  <strong>{m.body.subject || "无主题"}</strong>
                  <span>{m.body.text?.slice(0, 90)}</span>
                  <small>
                    {new Date(m.body.received_at).toLocaleString("zh-CN", {
                      timeZone: "Asia/Shanghai",
                    })}{" "}
                    · {m.body.index_status}
                  </small>
                </button>
              ))}
              <div className="actions">
                <button
                  disabled={!offset}
                  onClick={() => setOffset(Math.max(0, offset - 30))}
                >
                  上一页
                </button>
                <button
                  disabled={list.length < 30}
                  onClick={() => setOffset(offset + 30)}
                >
                  下一页
                </button>
              </div>
            </section>
            <section className="panel mail-reader">
              {opened ? (
                <>
                  <small>
                    {opened.body.sender} → {opened.body.to?.join(", ")}
                  </small>
                  <h2>{opened.body.subject}</h2>
                  {opened.body.incomplete && (
                    <p className="mail-warning">
                      历史资料缺少完整原始头部，收件时间可能为旧入库时间。
                    </p>
                  )}
                  <div className="actions">
                    <button onClick={() => void makeDraft("reply")}>
                      回复草稿
                    </button>
                    <button onClick={() => void makeDraft("reply_all")}>
                      回复全部
                    </button>
                    <button
                      onClick={() => {
                        setPage("assistant");
                        setSelected([opened.body.account_id]);
                        setSession("");
                        setTurns([]);
                        setQuery("总结这组往来并列出有证据的待办与截止时间");
                      }}
                    >
                      询问此线程
                    </button>
                    {["filtered", "review"].includes(opened.status) ? (
                      <button
                        onClick={() =>
                          void act(
                            () =>
                              api(`mail/messages/${opened.id}/state`, {
                                status: "active",
                              }),
                            "已放行并排队建立索引",
                          )
                        }
                      >
                        恢复处理
                      </button>
                    ) : (
                      <button
                        onClick={() =>
                          void act(
                            () =>
                              api(`mail/messages/${opened.id}/state`, {
                                status: "archived",
                              }),
                            "已在本地归档",
                          )
                        }
                      >
                        本地归档
                      </button>
                    )}
                  </div>
                  <pre className="mail-body">
                    {opened.body.raw_text || opened.body.text}
                  </pre>
                  {opened.body.attachments?.map((a: any) => (
                    <details key={a.id}>
                      <summary>
                        附件：{a.name} · {a.status}
                      </summary>
                      {a.segments?.map((s: any, i: number) => (
                        <p key={i}>
                          {s.location}：{s.text}
                        </p>
                      ))}
                    </details>
                  ))}
                  <details>
                    <summary>过滤依据与原始头部</summary>
                    <pre>
                      {pretty({
                        filter: opened.body.filter,
                        headers: opened.body.headers,
                      })}
                    </pre>
                  </details>
                  <h3>同一线程 · {thread?.messages.length || 0} 封</h3>
                  {thread?.messages.map((r: Row) => (
                    <button key={r.id} onClick={() => void openMail(r.id)}>
                      {r.body.subject} ·{" "}
                      {new Date(r.body.received_at).toLocaleString()}
                    </button>
                  ))}
                  <details>
                    <summary>手工关联到线程</summary>
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        const target = new FormData(e.currentTarget).get(
                          "target",
                        );
                        void act(
                          () =>
                            api(`mail/messages/${opened.id}/thread`, {
                              target_thread_id: target,
                            }),
                          "已关联",
                        );
                      }}
                    >
                      <input name="target" placeholder="目标线程 ID" required />
                      <button>关联</button>
                    </form>
                    <small>当前线程：{opened.body.thread_id}</small>
                  </details>
                </>
              ) : (
                <div className="empty">选择邮件查看正文、附件和往来线程。</div>
              )}
            </section>
          </div>
        </>
      )}
    </>
  );
}
