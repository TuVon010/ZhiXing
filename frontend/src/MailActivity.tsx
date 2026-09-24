import { useEffect, useState } from "react";
import { statusName, type RecordRow } from "./MailTrace";

type Api = (path: string, body?: unknown) => Promise<any>;
type ActivityRow = {
  id: string;
  title: string;
  kind: string;
  status: string;
  created_at: string;
};
export function MailActivity({
  api,
  account,
  notifications,
  onTrace,
}: {
  api: Api;
  account: string;
  notifications: boolean;
  onTrace: (id: string) => void;
}) {
  const [items, setItems] = useState<(ActivityRow & RecordRow)[]>([]),
    [total, setTotal] = useState(0),
    [offset, setOffset] = useState(0),
    [unread, setUnread] = useState(0),
    [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => setOffset(0), [account, notifications]);
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const result = await api(
          `mail/${notifications ? "notifications" : "activity"}?limit=30&offset=${offset}${account ? "&account_id=" + account : ""}`,
        );
        if (!cancelled) {
          setItems(result.items);
          setTotal(result.total);
          setUnread(result.unread || 0);
          setError("");
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    };
    void load();
    const timer = setInterval(() => void load(), 3000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [api, account, notifications, offset, revision]);
  async function read(id: string) {
    try {
      await api(`mail/notifications/${id}/read`, {});
      setRevision((x) => x + 1);
    } catch (e) {
      setError(String(e));
    }
  }
  async function remove(id: string) {
    try {
      await api(`mail/records/${id}/${notifications ? "trash" : "hide"}`, {});
      setRevision((x) => x + 1);
    } catch (e) {
      setError(String(e));
    }
  }
  return (
    <section className="panel">
      <h3>
        {notifications
          ? `通知与提醒 · ${unread} 条未读`
          : "Agent 与动作运行记录"}
      </h3>
      <p>
        {notifications
          ? "到期提醒、审批请求和执行结果保存在这里。"
          : "每 3 秒刷新；对话结束后，动作可能仍在排队或等待审批。"}{" "}
        当前范围：{account ? "所选邮箱" : "全部邮箱"}。
      </p>
      {error && <p role="alert">{error}</p>}
      {!items.length && <div className="empty">当前范围暂无记录。</div>}
      {items.map((row) => (
        <article className="mail-account" key={row.id}>
          <div>
            <h3>{notifications ? row.body.title : row.title}</h3>
            <p>
              {notifications
                ? row.status === "read"
                  ? "已读"
                  : "未读"
                : statusName(row.status)}{" "}
              ·{" "}
              {new Date(row.created_at).toLocaleString("zh-CN", {
                timeZone: "Asia/Shanghai",
              })}
            </p>
            {notifications && (
              <p>{row.body.content || statusName(row.body.status || "")}</p>
            )}
          </div>
          <div className="actions">
            {(!notifications || row.body.run_id) && (
              <button
                onClick={() =>
                  onTrace(notifications ? row.body.run_id : row.id)
                }
              >
                查看执行 Trace
              </button>
            )}
            {notifications && row.status !== "read" && (
              <button onClick={() => void read(row.id)}>标记已读</button>
            )}
            <button disabled={notifications ? !row.scope?.startsWith("web:mail:") : ["queued", "running", "waiting_approval"].includes(row.status)} onClick={() => void remove(row.id)}>{notifications ? "移入回收站" : "隐藏记录"}</button>
          </div>
        </article>
      ))}
      <div className="actions">
        <button
          disabled={!offset}
          onClick={() => setOffset(Math.max(0, offset - 30))}
        >
          上一页
        </button>
        <span>{total} 条记录</span>
        <button
          disabled={offset + 30 >= total}
          onClick={() => setOffset(offset + 30)}
        >
          下一页
        </button>
      </div>
    </section>
  );
}
