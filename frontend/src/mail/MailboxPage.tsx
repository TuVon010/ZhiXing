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
    setOpened,
    setTrace,
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
  async function feedback(change: Record<string, unknown>, message: string) {
    if (!opened) return;
    await act(async () => {
      await api(`mail/messages/${opened.id}/perception/feedback`, {
        ...change,
        message_id: opened.id,
      });
      setOpened(await api(`mail/messages/${opened.id}`));
    }, message);
  }
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
                  <span>
                    {m.body.perception?.summary || m.body.text?.slice(0, 90)}
                    {m.body.perception?.priority === "high" && (
                      <span className="mail-tag mail-tag-high">高优先</span>
                    )}
                    {m.body.perception?.category === "ad" && (
                      <span className="mail-tag mail-tag-ad">广告</span>
                    )}
                    {m.body.perception?.needs_reply && (
                      <span className="mail-tag mail-tag-reply">待回复</span>
                    )}
                  </span>
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
                  <button onClick={() => void act(() => api(`mail/messages/${opened.id}/perception`, {}), "已加入分析队列；完成后可刷新邮件查看结果")}>分析 / 重新分析</button>
                  {opened.body.perception && (
                    <div className="mail-perception">
                      <h3>AI 感知结果</h3>
                      <button onClick={() => void act(async () => setTrace(await api(`mail/traces/${opened.id}`)), "")}>查看感知 Trace</button>
                      <div className="mail-perception-grid">
                        <div>
                          <strong>摘要</strong>
                          <p>{opened.body.perception.summary || "（无）"}</p>
                        </div>
                        <div>
                          <strong>分类</strong>
                          <p>{categoryLabel(opened.body.perception.category)}</p>
                        </div>
                        <div>
                          <strong>优先级</strong>
                          <p>{priorityLabel(opened.body.perception.priority)}</p>
                        </div>
                        <div>
                          <strong>垃圾评分</strong>
                          <p>{(opened.body.perception.spam_score * 100).toFixed(0)}%</p>
                        </div>
                        <div>
                          <strong>需要回复</strong>
                          <p>{opened.body.perception.needs_reply ? "是" : "否"}</p>
                        </div>
                        <div>
                          <strong>置信度</strong>
                          <p>{(opened.body.perception.confidence * 100).toFixed(0)}%</p>
                        </div>
                      </div>
                      {opened.body.perception.todos?.length > 0 && (
                        <div>
                          <strong>提取的待办</strong>
                          <ul>
                            {opened.body.perception.todos.map((t: any, i: number) => (
                              <li key={i}>
                                {t.action}
                                {t.deadline && ` · 截止: ${new Date(t.deadline).toLocaleString("zh-CN")}`}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {opened.body.perception.calendar_events?.length > 0 && (
                        <div>
                          <strong>提取的日程</strong>
                          <ul>
                            {opened.body.perception.calendar_events.map((e: any, i: number) => (
                              <li key={i}>
                                {e.title}
                                {e.start && ` · ${new Date(e.start).toLocaleString("zh-CN")}`}
                                {e.location && ` · ${e.location}`}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {opened.body.perception.reasons?.length > 0 && (
                        <div className="mail-perception-reasons">
                          <strong>判断理由</strong>
                          <ul>
                            {opened.body.perception.reasons.map((r: string, i: number) => (
                              <li key={i}>{r}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      <details>
                        <summary>纠正感知结果</summary>
                        <div className="mail-perception-feedback">
                          <button onClick={() => void feedback({ category: "work" }, "已标记为工作邮件")}>标记为工作</button>
                          <button onClick={() => void feedback({ category: "personal" }, "已标记为个人邮件")}>标记为个人</button>
                          <button onClick={() => void feedback({ category: "ad" }, "已标记为广告")}>标记为广告</button>
                          <button onClick={() => void feedback({ spam_score: 0.9 }, "已移入本地过滤箱")}>标记垃圾</button>
                          <button onClick={() => void feedback({ spam_score: 0.0 }, "已恢复为正常邮件")}>标记正常</button>
                          <button onClick={() => void feedback({ priority: "high" }, "已标记为高优先")}>高优先</button>
                          <button onClick={() => void feedback({ priority: "low" }, "已标记为低优先")}>低优先</button>
                        </div>
                        <small>纠正会生成待确认的记忆候选；确认后才用于后续感知。</small>
                      </details>
                    </div>
                  )}
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

function categoryLabel(c: string): string {
  const map: Record<string, string> = {
    work: "工作",
    personal: "个人",
    ad: "广告/营销",
    notification: "系统通知",
    other: "其他",
  };
  return map[c] || c;
}

function priorityLabel(p: string): string {
  const map: Record<string, string> = {
    high: "高",
    normal: "普通",
    low: "低",
  };
  return map[p] || p;
}
