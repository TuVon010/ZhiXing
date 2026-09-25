import { useEffect, useState } from "react";
import { useWorkspace } from "./context";
import { label } from "./shared";

const kinds = [
  ["mail_message", "邮件"], ["mail_draft", "草稿"], ["todo", "待办"],
  ["calendar", "日程"], ["reminder", "提醒"], ["notification", "通知"],
  ["memory", "记忆"], ["assistant_session", "Agent 会话"],
  ["run", "动作运行"], ["assistant_turn", "Agent 轮次"],
  ["legacy_message", "旧项目来源记录"],
] as const;
type RecordSummary = {
  id: string; kind: string; status: string; scope: string; title: string;
  created_at: string; origin: string; hidden?: boolean;
};

export function RecordsPage() {
  const { page, account, api } = useWorkspace();
  const [kind, setKind] = useState<string>("mail_message");
  const [trashed, setTrashed] = useState(false);
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [overview, setOverview] = useState<any>(null);
  const [items, setItems] = useState<RecordSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => setOffset(0), [kind, account, trashed]);
  useEffect(() => {
    if (page !== "records") return;
    let active = true;
    const query = new URLSearchParams({ kind, limit: "30", offset: String(offset) });
    if (account && kind !== "legacy_message") query.set("account_id", account);
    if (trashed && kind !== "legacy_message") query.set("trashed", "true");
    void Promise.all([api("mail/records/overview"), api("mail/records/list?" + query)])
      .then(([summary, list]) => {
        if (active) { setOverview(summary); setItems(list.items); setTotal(list.total); setError(""); }
      })
      .catch((e) => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, [page, account, kind, trashed, offset, revision, api]);
  async function change(path: string, message: string) {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await api(path, {});
      setNotice(result.backup_path ? `备份已保存：${result.backup_path}` : message);
      setRevision((n) => n + 1);
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  if (page !== "records") return null;
  return <>
    <section className="panel">
      <h3>本地记录管理</h3>
      <p>这里只管理项目本地记录，不会删除邮箱服务器上的邮件。待办、日程和提醒移入回收站后可以恢复；运行 Trace 与审计只可从列表隐藏。</p>
      <p>当前主库：{overview?.current_mail_messages ?? "…"} 封新版邮件，{overview?.trash ?? "…"} 条在回收站。测试执行资料单独保存在项目的 artifacts/test-runs。</p>
      {overview && <p>旧项目：{overview.legacy_messages} 条旧来源记录、{overview.legacy_runs} 条旧运行（其中 {overview.legacy_pending_runs} 条未结束）。旧邮件来源字段无法证明是真实邮件还是测试样本，当前保留并隔离。</p>}
      <div className="actions">
        <label>记录类型 <select aria-label="记录类型" value={kind} onChange={(e) => setKind(e.target.value)}>
          {kinds.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
        </select></label>
        {kind !== "legacy_message" && <label><input type="checkbox" checked={trashed} onChange={(e) => setTrashed(e.target.checked)} /> 查看回收站</label>}
      </div>
      {kind === "legacy_message" && <>
        <p>{overview?.legacy_note} 如需后续清理，先生成独立备份并逐项核对来源。</p>
        <button disabled={busy} onClick={() => void change("mail/records/legacy/backup", "旧项目数据备份已保存到项目 backups 目录")}>生成旧数据备份</button>
      </>}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
    </section>
    <section className="panel">
      <h3>记录列表 · {total} 条</h3>
      {!items.length && <p className="empty">当前范围暂无记录。</p>}
      {items.map((item) => <article className="mail-account" key={item.id}>
        <div><strong>{item.title}</strong><p>{label[item.status] || item.status} · {new Date(item.created_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}</p><small>{item.origin || item.scope} · {item.id.slice(0, 12)}</small></div>
        <div className="actions">
          {kind === "legacy_message" ? <small>旧资料保留，先核对来源</small> :
            kind === "run" || kind === "assistant_turn" ?
              <button disabled={busy || ["queued", "running", "waiting_approval"].includes(item.status)} onClick={() => void change(`mail/records/${item.id}/${item.hidden ? "unhide" : "hide"}`, item.hidden ? "已显示运行记录" : "已从列表隐藏；Trace 仍保留")}>{item.hidden ? "取消隐藏" : "隐藏记录"}</button> :
              trashed ? <button disabled={busy} onClick={() => void change(`mail/records/${item.id}/restore`, "记录已恢复")}>恢复</button> :
                <button disabled={busy} onClick={() => void change(`mail/records/${item.id}/trash`, "已移入本地回收站")}>移入回收站</button>}
        </div>
      </article>)}
      <div className="actions"><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 30))}>上一页</button><span>{total ? offset + 1 : 0}—{Math.min(offset + 30, total)} / {total}</span><button disabled={offset + 30 >= total} onClick={() => setOffset(offset + 30)}>下一页</button></div>
    </section>
  </>;
}
