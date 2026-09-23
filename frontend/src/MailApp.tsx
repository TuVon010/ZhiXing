import { BillingSummary } from "./PricingSettings";
import "./mail.css";
import { MailTrace } from "./MailTrace";
import { MailActivity } from "./MailActivity";
import { MailCalendar } from "./MailCalendar";
import { WorkspaceContext } from "./mail/context";
import { useMailWorkspace } from "./mail/useMailWorkspace";
import { pretty, blankAccount, type Api, type Row } from "./mail/shared";
import { MailboxPage } from "./mail/MailboxPage";
import { AccountsPage } from "./mail/AccountsPage";
import { AssistantPage } from "./mail/AssistantPage";
import { FollowupsPage } from "./mail/FollowupsPage";
import { ApprovalsPage } from "./mail/ApprovalsPage";
import { DraftsPage } from "./mail/DraftsPage";
import { ImportsPage } from "./mail/ImportsPage";
import { MemoryPage } from "./mail/MemoryPage";
import { SettingsPage } from "./mail/SettingsPage";
export function MailApp({ api }: { api: Api }) {
  const workspace = useMailWorkspace(api);
  const {
    page,
    accounts,
    account,
    setAccount,
    setOffset,
    config,
    error,
    setError,
    notice,
    setOpened,
    setAccountForm,
    selected,
    setSelected,
    setSession,
    setTurns,
    trace,
    setTrace,
    imports,
    memory,
    loadAccounts,
    refresh,
    act,
    navigate,
    unread,
  } = workspace;
  const nav = [
    ["inbox", "收件箱"],
    ["followups", "待办与跟进"],
    ["approvals", "审批中心"],
    ["activity", "运行记录"],
    ["notifications", "通知与提醒"],
    ["calendar", "日程"],
    ["assistant", "邮件 Agent"],
    ["search", "知识检索"],
    ["drafts", "回复草稿"],
    ["filter", "过滤箱"],
    ["imports", "收取与导入"],
    ["accounts", "邮箱账号"],
    ["memory", "记忆"],
    ["settings", "设置与计价"],
  ];
  return (
    <WorkspaceContext.Provider value={workspace}>
      <div className="mail-shell">
        <aside className="mail-sidebar">
          <div className="brand">
            <img src="/zhixing-mark.svg" width="38" alt="知行标志" />
            <div className="brand-name">
              知行<small>MAIL AGENT</small>
            </div>
          </div>
          <p className="mail-subtitle">从邮件中，找到下一步。</p>
          <nav>
            {[
              ["邮件", ["inbox", "drafts", "filter"]],
              [
                "工作",
                [
                  "assistant",
                  "search",
                  "followups",
                  "calendar",
                  "approvals",
                  "notifications",
                  "activity",
                ],
              ],
              ["管理", ["accounts", "imports", "memory", "settings"]],
            ].map(([title, ids]) => (
              <section className="mail-nav-group" key={String(title)}>
                <small>{title}</small>
                {nav
                  .filter(([id]) => ids.includes(id))
                  .map(([id, name]) => (
                    <button
                      key={id}
                      className={page === id ? "selected" : ""}
                      onClick={() => navigate(id)}
                    >
                      {name}
                      {id === "notifications" && unread > 0 && (
                        <span
                          className="mail-badge"
                          aria-label={`${unread} 条未读通知`}
                        >
                          {unread > 99 ? "99+" : unread}
                        </span>
                      )}
                    </button>
                  ))}
              </section>
            ))}
          </nav>
          <div className="mail-local">● 本地索引 · 人工确认发送</div>
        </aside>
        <main className="mail-main">
          <header>
            <div>
              <span className="eyebrow">ZHIXING / PERSONAL MAIL AGENT</span>
              <h1>{nav.find(([id]) => id === page)?.[1]}</h1>
            </div>
            <div className="mail-toolbar">
              <span>
                {config.mode === "demo" ? "离线演示模式" : "真实模型模式"}
              </span>
              <select
                aria-label="当前邮箱"
                value={account}
                onChange={(e) => {
                  setAccount(e.target.value);
                  setOffset(0);
                  setOpened(null);
                  setSession("");
                  setTurns([]);
                  setSelected(e.target.value ? [e.target.value] : []);
                }}
              >
                <option value="">全部邮箱（仅浏览）</option>
                {accounts.map((a) => (
                  <option value={a.id} key={a.id}>
                    {a.body.name} · {a.body.address}
                  </option>
                ))}
              </select>
              <button onClick={() => void act(() => refresh(), "已刷新")}>
                刷新
              </button>
            </div>
          </header>
          {error && (
            <div role="alert" className="alert">
              {error}
              <button onClick={() => setError("")}>×</button>
            </div>
          )}
          {notice && (
            <div role="status" className="notice">
              {notice}
            </div>
          )}
          {!accounts.length && (
            <section className="panel mail-welcome">
              <h2>让知行成为你的邮件工作助理</h2>
              <p>
                连接多个邮箱，保留来源、查找证据、整理待办。新账号默认暂停，连接测试不会发送邮件。
              </p>
              <button
                onClick={() => {
                  navigate("accounts");
                  setAccountForm({ body: { ...blankAccount } });
                }}
              >
                添加邮箱
              </button>
              {config.mode === "demo" && (
                <button
                  onClick={() =>
                    void act(async () => {
                      const r = await api("mail/demo", {});
                      await loadAccounts();
                      setAccount(r.account_id);
                      setSelected([r.account_id]);
                      await refresh();
                    }, "已导入明确标记的演示邮件")
                  }
                >
                  导入演示邮件
                </button>
              )}
            </section>
          )}
          <MailboxPage />
          <AccountsPage />
          <AssistantPage />
          <FollowupsPage />
          <ApprovalsPage />
          <DraftsPage />
          <ImportsPage />
          <MemoryPage />
          <SettingsPage />
          {trace?.root && (
            <MailTrace
              data={trace}
              onClose={() => setTrace(null)}
              onRefresh={() =>
                void act(
                  async () => setTrace(await api("mail/traces/" + trace.id)),
                  "已刷新执行结果",
                )
              }
            />
          )}
          {["activity", "notifications"].includes(page) && (
            <MailActivity
              key={page}
              api={api}
              account={account}
              notifications={page === "notifications"}
              onTrace={(id) =>
                void act(
                  async () => setTrace(await api("mail/traces/" + id)),
                  "",
                )
              }
            />
          )}
          {page === "calendar" && (
            <MailCalendar
              api={api}
              account={account}
              onTrace={(id) =>
                void act(
                  async () => setTrace(await api("mail/traces/" + id)),
                  "",
                )
              }
            />
          )}
          {trace && !trace.root && (
            <div className="modal">
              <section className="panel mail-trace">
                <div className="mail-sectionbar">
                  <h2>Agent Trace / 迁移记录</h2>
                  <button onClick={() => setTrace(null)}>关闭</button>
                </div>
                <BillingSummary value={trace.billing} />
                {trace.runs?.map((r: Row) => (
                  <p key={r.id}>
                    {r.body.summary || r.id}
                    <button
                      onClick={() =>
                        void act(
                          () => api(`mail/legacy/${r.id}/resume`, {}),
                          "已记录人工迁移复核",
                        )
                      }
                    >
                      确认恢复此旧运行
                    </button>
                  </p>
                ))}
                <button
                  onClick={() => {
                    const url = URL.createObjectURL(
                      new Blob([pretty(trace)], { type: "application/json" }),
                    );
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = "zhixing-mail-trace.json";
                    a.click();
                    setTimeout(() => URL.revokeObjectURL(url), 1000);
                  }}
                >
                  导出邮件 Trace JSON
                </button>
                <pre>{pretty(trace)}</pre>
              </section>
            </div>
          )}
          <footer>知行 · 邮件是证据，记忆需确认，发送经审批。</footer>
        </main>
      </div>
    </WorkspaceContext.Provider>
  );
}
