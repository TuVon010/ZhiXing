import { useWorkspace } from "./context";
import { pretty, label } from "./shared";
export function AssistantPage() {
  const {
    page,
    setPage,
    accounts,
    error,
    busy,
    setOpened,
    query,
    setQuery,
    selected,
    setSelected,
    searchResult,
    setSearchResult,
    session,
    setSession,
    turns,
    setTurns,
    setTrace,
    sessions,
    act,
    waitJob,
    openMail,
    ask,
    api,
  } = useWorkspace();
  return (
    <>
      {["search", "assistant"].includes(page) && (
        <>
          {page === "assistant" && (
            <section className="panel">
              <label>
                历史会话
                <select
                  aria-label="历史会话"
                  value={session}
                  onChange={(e) => {
                    const id = e.target.value;
                    setSession(id);
                    setTurns([]);
                    setOpened(null);
                    if (id)
                      void act(async () => {
                        const detail = await api("assistant/sessions/" + id);
                        setSelected(detail.session.body.account_ids);
                        setTurns(detail.turns);
                      }, "已恢复会话及原账号范围");
                  }}
                >
                  <option value="">新会话</option>
                  {sessions.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.id.slice(0, 8)} ·{" "}
                      {s.body.account_ids
                        .map(
                          (id: string) =>
                            accounts.find((a) => a.id === id)?.body.name || id,
                        )
                        .join("、")}
                    </option>
                  ))}
                </select>
              </label>
            </section>
          )}
          <section className="panel">
            <h3>本次允许读取的邮箱</h3>
            <div className="mail-scope">
              {accounts.map((a) => (
                <label key={a.id}>
                  <input
                    type="checkbox"
                    checked={selected.includes(a.id)}
                    onChange={(e) => {
                      setSelected(
                        e.target.checked
                          ? [...selected, a.id]
                          : selected.filter((id) => id !== a.id),
                      );
                      setSession("");
                      setTurns([]);
                      setSearchResult(null);
                    }}
                  />
                  {a.body.name} · {a.body.address}
                </label>
              ))}
            </div>
            <p>
              跨邮箱必须显式选择；生成回答时会将选中的证据交给已配置的主模型。
            </p>
            <textarea
              aria-label="邮件问题"
              rows={4}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="例如：导师最近要求修改哪些实验？有哪些承诺还未完成？"
            />
            <div className="actions">
              <button
                className="primary"
                disabled={busy || !query.trim() || !selected.length}
                onClick={() =>
                  void (page === "search"
                    ? act(async () =>
                        setSearchResult(
                          await waitJob(
                            (
                              await api("search", {
                                account_ids: selected,
                                query,
                              })
                            ).job_id,
                          ),
                        ),
                      )
                    : ask())
                }
              >
                {page === "search" ? "检索证据" : "交给 Agent"}
              </button>
              {page === "assistant" && (
                <>
                  <button
                    onClick={() => {
                      setSession("");
                      setTurns([]);
                      setTrace(null);
                    }}
                  >
                    新建会话
                  </button>
                  <button
                    disabled={!session}
                    onClick={() =>
                      void act(async () =>
                        setTurns(
                          (await api("assistant/sessions/" + session)).turns,
                        ),
                      )
                    }
                  >
                    刷新会话
                  </button>
                </>
              )}
            </div>
          </section>
          {searchResult && (
            <section className="panel">
              <h3>检索结果 · {searchResult.mode}</h3>
              <p>
                {searchResult.degraded
                  ? "已降级 / 部分索引未完成：" + searchResult.reason
                  : "检索已完成"}{" "}
                · {searchResult.latency_ms} ms
              </p>
              {searchResult.evidence.map((e: any) => (
                <article className="mail-evidence" key={e.id}>
                  <button
                    onClick={() => {
                      setPage("inbox");
                      void openMail(e.message_id);
                    }}
                  >
                    查看来源 · {e.location}
                  </button>
                  <p>{e.text}</p>
                </article>
              ))}
              <details>
                <summary>检索排名与版本</summary>
                <pre>{pretty(searchResult)}</pre>
              </details>
            </section>
          )}
          {page === "assistant" &&
            turns.map((t) => (
              <section className="panel" key={t.id}>
                <small>{label[t.status] || t.status}</small>
                <h3>{t.body.text}</h3>
                <p className="mail-answer">{t.body.answer || "正在处理…"}</p>
                {t.body.error && <pre>{pretty(t.body.error)}</pre>}
                {t.body.citations?.map((id: string) => {
                  const e = t.body.evidence.find((v: any) => v.id === id);
                  return e ? (
                    <button
                      key={id}
                      onClick={() => {
                        setPage("inbox");
                        void openMail(e.message_id);
                      }}
                    >
                      来源：{e.location}
                    </button>
                  ) : null;
                })}
                <div className="actions">
                  <button
                    onClick={() =>
                      void act(async () =>
                        setTrace(await api("mail/traces/" + t.id)),
                      )
                    }
                  >
                    查看 Agent Trace
                  </button>
                  {["running", "queued"].includes(t.status) && (
                    <button
                      onClick={() =>
                        void act(() =>
                          api("assistant/turns/" + t.id + "/cancel", {}),
                        )
                      }
                    >
                      取消本轮
                    </button>
                  )}
                </div>
                <details>
                  <summary>工具调用步骤</summary>
                  <pre>{pretty(t.body.steps)}</pre>
                </details>
              </section>
            ))}
        </>
      )}
    </>
  );
}
