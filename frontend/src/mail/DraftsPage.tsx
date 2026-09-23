import { useWorkspace } from "./context";
import { label } from "./shared";
export function DraftsPage() {
  const {
    page,
    accounts,
    account,
    list,
    busy,
    draftForm,
    setDraftForm,
    setTrace,
    act,
    navigate,
    makeDraft,
    draftPayload,
    api,
  } = useWorkspace();
  return (
    <>
      {page === "drafts" && (
        <>
          <section className="panel">
            <button onClick={() => void makeDraft()}>新邮件草稿</button>
            {list.map((d) => (
              <article className="mail-account" key={d.id}>
                <div>
                  <h3>{d.body.subject || "无主题草稿"}</h3>
                  <p>
                    {d.body.to?.join(", ")} · {label[d.status] || d.status} · v
                    {d.body.version}
                  </p>
                </div>
                <div className="actions">
                  <button onClick={() => setDraftForm(d)}>打开草稿</button>
                  {d.body.approval_runs?.map((id: string) => (
                    <button
                      key={id}
                      onClick={() =>
                        void act(
                          async () => setTrace(await api("mail/traces/" + id)),
                          "",
                        )
                      }
                    >
                      查看发送结果
                    </button>
                  ))}
                </div>
              </article>
            ))}
          </section>
          {draftForm && (
            <section className="panel">
              <h3>编辑草稿 · v{draftForm.body.version}</h3>
              <p>
                发件邮箱：
                {
                  accounts.find((a) => a.id === draftForm.body.account_id)?.body
                    .address
                }{" "}
                · {label[draftForm.status] || draftForm.status}
              </p>
              {[
                ["to", "收件人"],
                ["cc", "抄送"],
                ["subject", "邮件主题"],
                ["content", "邮件正文"],
              ].map(([k, n]) => (
                <label className="mail-field" key={k}>
                  {n}
                  <textarea
                    rows={k === "content" ? 10 : 1}
                    aria-label={n}
                    value={
                      ["to", "cc"].includes(k)
                        ? draftForm.body[k].join(", ")
                        : draftForm.body[k]
                    }
                    onChange={(e) =>
                      setDraftForm({
                        ...draftForm,
                        dirty: true,
                        body: {
                          ...draftForm.body,
                          [k]: ["to", "cc"].includes(k)
                            ? e.target.value.split(",").map((v) => v.trim())
                            : e.target.value,
                        },
                      })
                    }
                  />
                </label>
              ))}
              <p>
                编辑后需要先保存；保存会使旧发送审批失效。审批内容必须与当前草稿版本一致。
              </p>
              <div className="actions">
                <button
                  disabled={busy}
                  onClick={() =>
                    void act(
                      async () =>
                        setDraftForm(
                          await api(
                            "mail/drafts/" + draftForm.id,
                            draftPayload(),
                          ),
                        ),
                      "草稿已保存，旧审批已失效",
                    )
                  }
                >
                  保存草稿
                </button>
                <button
                  disabled={
                    busy || draftForm.dirty || draftForm.status !== "draft"
                  }
                  onClick={() =>
                    void act(async () => {
                      await api("mail/drafts/" + draftForm.id + "/submit", {
                        version: draftForm.body.version,
                      });
                      setDraftForm({ ...draftForm, status: "approval" });
                    }, "已提交审批，请前往审批中心核对收件人与正文")
                  }
                >
                  申请发送审批
                </button>
                <button onClick={() => navigate("approvals")}>
                  前往审批中心
                </button>
              </div>
            </section>
          )}
        </>
      )}
    </>
  );
}
