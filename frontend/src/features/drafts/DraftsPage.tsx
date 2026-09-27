import { useEffect, useRef, useState } from "react";
import { useWorkspace } from "../../app/workspace/context";
import { label } from "../../shared/mail";

export function DraftsPage() {
  const [confirmSend, setConfirmSend] = useState(false);
  const contentRef = useRef<HTMLTextAreaElement | null>(null);
  const { page, accounts, drafts, busy, draftForm, setDraftForm, setTrace,
    act, makeDraft, draftPayload, refresh, api } = useWorkspace();

  useEffect(() => {
    if (page === "drafts" && draftForm) contentRef.current?.focus({ preventScroll: true });
  }, [page, draftForm?.id]);
  if (page !== "drafts") return null;

  const editable = drafts.filter((draft) => draft.status === "draft");
  const history = drafts.filter((draft) => draft.status !== "draft");
  const visible = [...editable, ...history];
  const openDraft = (draft: any) => {
    setConfirmSend(false);
    setDraftForm(draft);
  };
  const generateReply = async () => {
    await act(async () => {
      const generated = await api(`mail/drafts/${draftForm.id}/suggest`, {});
      setDraftForm(generated);
      await refresh("drafts");
    }, "AI 回复建议已生成，请核对事实、语气和收件人", false);
  };

  return <>
    <section className="draft-intro">
      <div>
        <p>“待回复”是 Agent 的判断；“AI 起草”才会生成正文。任何邮件都只会在你最终确认后发送。</p>
      </div>
      <button className="primary" onClick={() => void makeDraft()}>新邮件草稿</button>
    </section>

    <div className={`draft-workspace ${draftForm ? "has-selection" : ""}`}>
      <section className="panel draft-list" aria-label="草稿列表">
        <div className="mail-sectionbar"><div>
          <h3>草稿</h3><small>{editable.length} 封待编辑 · {history.length} 封历史记录</small>
        </div></div>
        {!visible.length && <div className="empty">暂无草稿。可以从收件箱选择“手动回复”或“AI 起草”。</div>}
        {visible.map((draft) => <article
          className={`draft-list-item ${draftForm?.id === draft.id ? "selected" : ""}`} key={draft.id}>
          <button className="draft-open" onClick={() => openDraft(draft)}>
            <strong>{draft.body.subject || "无主题草稿"}</strong>
            <span>{draft.body.to?.join("、") || "尚未填写收件人"}</span>
            <small>{label[draft.status] || draft.status} · v{draft.body.version}</small>
          </button>
          {draft.status === "draft" && <button className="draft-delete" disabled={busy}
            aria-label={`删除草稿：${draft.body.subject || "无主题草稿"}`}
            onClick={() => void act(async () => {
              await api(`mail/records/${draft.id}/trash`, {});
              if (draftForm?.id === draft.id) setDraftForm(null);
            }, "草稿已移入回收站", "drafts")}>删除</button>}
        </article>)}
      </section>

      {draftForm ? <section className="panel draft-editor" aria-label="草稿编辑器">
        <div className="mail-sectionbar">
          <div>
            <span className="eyebrow">{draftForm.body.ai_suggestion ? "AI REPLY DRAFT" : "MAIL DRAFT"}</span>
            <h3>编辑草稿 · v{draftForm.body.version}</h3>
            <small>发件邮箱：{accounts.find((a) => a.id === draftForm.body.account_id)?.body.address}
              {" · "}{label[draftForm.status] || draftForm.status}</small>
          </div>
          <button onClick={() => { setDraftForm(null); setConfirmSend(false); }}>关闭</button>
        </div>

        {draftForm.body.ai_suggestion && <div className="draft-ai-note">
          <strong>AI 回复建议</strong>
          <span>语气：{draftForm.body.ai_suggestion.tone}</span>
          {draftForm.body.ai_suggestion.key_points?.length > 0 &&
            <span>回复要点：{draftForm.body.ai_suggestion.key_points.join("；")}</span>}
          <small>已参考当前邮件线程。请核对事实，AI 不会替你发送。</small>
        </div>}

        {[["to", "收件人"], ["cc", "抄送"], ["subject", "邮件主题"], ["content", "邮件正文"]]
          .map(([key, name]) => <label className="mail-field" key={key}>{name}
            <textarea ref={key === "content" ? contentRef : undefined}
              rows={key === "content" ? 12 : 1} aria-label={name}
              disabled={draftForm.status !== "draft"}
              value={["to", "cc"].includes(key) ? draftForm.body[key].join(", ") : draftForm.body[key]}
              onChange={(event) => setDraftForm({ ...draftForm, dirty: true, body: {
                ...draftForm.body, [key]: ["to", "cc"].includes(key)
                  ? event.target.value.split(",").map((value) => value.trim()) : event.target.value,
              } })} />
          </label>)}

        {draftForm.status === "draft" && <div className="draft-editor-actions">
          <div className="actions">
            {draftForm.body.message_id && <button disabled={busy || draftForm.dirty}
              onClick={() => void generateReply()}>
              {draftForm.body.ai_suggestion ? "重新生成 AI 回复" : "AI 生成回复"}
            </button>}
            {draftForm.body.ai_suggestion && <button disabled={busy} onClick={() => void act(
              async () => setTrace(await api("mail/traces/" + draftForm.id)), "", false,
            )}>查看生成 Trace</button>}
            <button disabled={busy || !draftForm.dirty} onClick={() => void act(async () => {
              setDraftForm(await api("mail/drafts/" + draftForm.id, draftPayload()));
            }, "草稿已保存，旧的发送确认已失效", "drafts")}>保存草稿</button>
            <button className="primary" disabled={busy || draftForm.dirty ||
              !draftForm.body.to?.some((value: string) => value.trim()) || !draftForm.body.content?.trim()}
              onClick={() => setConfirmSend(true)}>核对并发送</button>
          </div>
          {draftForm.dirty && <small>有未保存的修改，保存后才能发送。</small>}
        </div>}

        {draftForm.body.approval_runs?.map((id: string) => <button key={id} onClick={() => void act(
          async () => setTrace(await api("mail/traces/" + id)), "", false,
        )}>查看发送结果</button>)}

        {confirmSend && <section className="send-confirmation" aria-label="最终发送确认">
          <span className="eyebrow">FINAL CHECK</span><h3>确认发送这封邮件？</h3>
          <dl className="approval-fields">
            <div><dt>发件邮箱</dt><dd>{accounts.find((a) => a.id === draftForm.body.account_id)?.body.address}</dd></div>
            <div><dt>收件人</dt><dd>{draftForm.body.to.join("、")}</dd></div>
            {draftForm.body.cc.length > 0 && <div><dt>抄送</dt><dd>{draftForm.body.cc.join("、")}</dd></div>}
            <div><dt>主题</dt><dd>{draftForm.body.subject}</dd></div>
            <div><dt>正文</dt><dd>{draftForm.body.content}</dd></div>
          </dl>
          <p>发送后不能撤回。系统会冻结当前版本，并用执行账本防止重复发送。</p>
          <div className="actions"><button disabled={busy} onClick={() => setConfirmSend(false)}>返回修改</button>
            <button className="primary" disabled={busy} onClick={() => void act(async () => {
              const result = await api("mail/drafts/" + draftForm.id + "/send", { version: draftForm.body.version });
              setDraftForm({ ...draftForm, status: "submitted",
                body: { ...draftForm.body, approval_runs: [result.run_id] } });
              setConfirmSend(false);
            }, "发送请求已进入安全执行队列，可在这里查看结果", "drafts")}>确认发送</button>
          </div>
        </section>}
      </section> : <section className="panel draft-empty-editor">
        <span className="eyebrow">DRAFT WORKSPACE</span><h2>选择一封草稿开始编辑</h2>
        <p>从收件箱进入时，新草稿会直接在这里打开。你也可以创建新邮件，或者选择已有草稿继续处理。</p>
        <button onClick={() => void makeDraft()}>新邮件草稿</button>
      </section>}
    </div>
  </>;
}
