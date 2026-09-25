import { useState } from "react";
import { useWorkspace } from "./context";
import { label } from "./shared";
export function DraftsPage() {
  const [confirmSend, setConfirmSend] = useState(false);
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
                  <button onClick={() => { setConfirmSend(false); setDraftForm(d); }}>打开草稿</button>
                  {d.status === "draft" && <button disabled={busy} onClick={() => void act(async () => {
                    await api(`mail/records/${d.id}/trash`, {});
                    if (draftForm?.id === d.id) setDraftForm(null);
                  }, "草稿已移入回收站")}>删除草稿</button>}
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
                编辑后需要先保存。最终确认只授权当前草稿版本；之后修改收件人、主题或正文必须重新确认。
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
                      "草稿已保存，旧的发送确认已失效",
                    )
                  }
                >
                  保存草稿
                </button>
                <button
                  disabled={
                    busy || draftForm.dirty || draftForm.status !== "draft" ||
                    !draftForm.body.to?.some((value: string) => value.trim()) ||
                    !draftForm.body.content?.trim()
                  }
                  onClick={() => setConfirmSend(true)}
                >
                  准备发送
                </button>
              </div>
              {confirmSend && <section className="send-confirmation" aria-label="最终发送确认">
                <span className="eyebrow">FINAL CHECK</span>
                <h3>确认发送这封邮件？</h3>
                <dl className="approval-fields">
                  <div><dt>发件邮箱</dt><dd>{accounts.find((a) => a.id === draftForm.body.account_id)?.body.address}</dd></div>
                  <div><dt>收件人</dt><dd>{draftForm.body.to.join("、")}</dd></div>
                  {draftForm.body.cc.length > 0 && <div><dt>抄送</dt><dd>{draftForm.body.cc.join("、")}</dd></div>}
                  <div><dt>主题</dt><dd>{draftForm.body.subject}</dd></div>
                  <div><dt>正文</dt><dd>{draftForm.body.content}</dd></div>
                </dl>
                <p>发送后不能撤回。系统会冻结当前版本并使用执行账本防止重复发送。</p>
                <div className="actions">
                  <button disabled={busy} onClick={() => setConfirmSend(false)}>返回修改</button>
                  <button className="primary" disabled={busy} onClick={() => void act(async () => {
                    const result = await api("mail/drafts/" + draftForm.id + "/send", { version: draftForm.body.version });
                    setDraftForm({ ...draftForm, status: "submitted",
                      body: { ...draftForm.body, approval_runs: [result.run_id] } });
                    setConfirmSend(false);
                  }, "发送请求已进入安全执行队列，可通过“查看发送结果”跟踪")}>确认发送</button>
                </div>
              </section>}
            </section>
          )}
        </>
      )}
    </>
  );
}
