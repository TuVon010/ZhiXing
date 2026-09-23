import { useWorkspace } from "./context";
import { pretty, label, actionLabel } from "./shared";
export function ApprovalsPage() {
  const { page, accounts, list, busy, setTrace, act, api } = useWorkspace();
  const fields: Record<string, string> = {
    account_id: "邮箱",
    to: "收件人",
    cc: "抄送",
    recipient: "收件人",
    subject: "主题",
    content: "正文",
    title: "标题",
    start: "开始时间",
    end: "结束时间",
    deadline: "截止时间",
    due_at: "提醒时间",
    status: "目标状态",
    description: "说明",
    owner: "负责人",
    commitment_target: "承诺对象",
    mode: "回复方式",
  };
  const technical = new Set([
    "draft_id",
    "draft_version",
    "draft_hash",
    "version",
    "message_id",
    "thread_id",
    "in_reply_to",
    "references",
    "id",
  ]);
  return (
    <>
      {page === "approvals" && (
        <section className="panel">
          <h3>待审批动作</h3>
          <p>
            请核对账号、动作参数和完整正文。批准后可查看执行结果；SMTP
            接受不代表邮件已经送达。
          </p>
          {list.length === 0 && (
            <div className="empty">当前范围内没有待审批动作。</div>
          )}
          {list.map((a) => (
            <article className="mail-account" key={a.id}>
              <div>
                <h3>
                  {actionLabel[a.body.action?.tool] ||
                    a.body.summary ||
                    "邮件动作"}{" "}
                  · {a.body.action?.args?.subject || ""}
                </h3>
                <p>
                  {label[a.status] || a.status} · v{a.body.version}
                </p>
                <dl className="approval-fields">
                  {Object.entries(a.body.action?.args || {})
                    .filter(([key]) => !technical.has(key))
                    .map(([key, value]) => (
                      <div key={key}>
                        <dt>{fields[key] || key}</dt>
                        <dd>
                          {key === "account_id"
                            ? accounts.find((account) => account.id === value)
                                ?.body.address || String(value)
                            : Array.isArray(value)
                              ? value.join("、")
                              : String(value ?? "未填写")}
                        </dd>
                      </div>
                    ))}
                </dl>
                <details>
                  <summary>完整参数与版本（调试）</summary>
                  <pre>{pretty(a.body.action?.args || a.body)}</pre>
                </details>
              </div>
              <button
                onClick={() =>
                  void act(
                    async () =>
                      setTrace(await api("mail/traces/" + a.body.run_id)),
                    "",
                  )
                }
              >
                查看执行 Trace
              </button>
              {a.status === "pending" && (
                <div className="actions">
                  <button
                    disabled={busy}
                    onClick={() =>
                      void act(
                        () =>
                          api("approvals/" + a.id, {
                            decision: "reject",
                            version: a.body.version,
                          }),
                        "已拒绝",
                      )
                    }
                  >
                    拒绝
                  </button>
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={() =>
                      void act(
                        () =>
                          api("approvals/" + a.id, {
                            decision: "approve",
                            version: a.body.version,
                          }),
                        "已批准，执行结果可在 Trace 查看",
                      )
                    }
                  >
                    批准执行
                  </button>
                </div>
              )}
            </article>
          ))}
        </section>
      )}
    </>
  );
}
