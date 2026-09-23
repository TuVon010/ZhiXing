import { useEffect, useState } from "react";
import { statusName, type RecordRow } from "./MailTrace";
type Api = (path: string, body?: unknown) => Promise<any>;
const localTime = (value: string) => {
  const d = new Date(value);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
};
export function MailCalendar({
  api,
  account,
  onTrace,
}: {
  api: Api;
  account: string;
  onTrace: (id: string) => void;
}) {
  const [items, setItems] = useState<RecordRow[]>([]),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState({ id: "", title: "", start: "", end: "" });
  useEffect(() => {
    let active = true;
    setDraft({ id: "", title: "", start: "", end: "" });
    setNotice("");
    async function load() {
      try {
        const data = await api(
          "mail/followups" + (account ? "?account_id=" + account : ""),
        );
        if (active)
          setItems(data.items.filter((r: RecordRow) => r.kind === "calendar"));
      } catch (e) {
        if (active) setError(String(e));
      }
    }
    void load();
    const timer = setInterval(() => void load(), 3000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [account, api]);
  async function save() {
    setBusy(true);
    setError("");
    try {
      await api("mail/calendar" + (draft.id ? "/" + draft.id : ""), {
        account_id: account,
        title: draft.title,
        start: new Date(draft.start).toISOString(),
        end: new Date(draft.end).toISOString(),
      });
      setNotice("日程已提交审批，请到审批中心确认。");
      setDraft({ id: "", title: "", start: "", end: "" });
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <section className="panel">
        <h3>{draft.id ? "修改日程" : "新建日程"}</h3>
        <p>仅保存在本地；创建和修改经审批后生效。时间按本机时区输入。</p>
        {error && <p role="alert">{error}</p>}
        {notice && <p role="status">{notice}</p>}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <label className="mail-field">
            日程标题
            <input
              required
              value={draft.title}
              onChange={(e) => setDraft({ ...draft, title: e.target.value })}
            />
          </label>
          <div className="pricing-grid">
            {(["start", "end"] as const).map((key) => (
              <label key={key}>
                {key === "start" ? "开始时间" : "结束时间"}
                <input
                  required
                  type="datetime-local"
                  value={draft[key]}
                  onChange={(e) =>
                    setDraft({ ...draft, [key]: e.target.value })
                  }
                />
              </label>
            ))}
          </div>
          <button disabled={!account || busy}>提交日程审批</button>
          {!account && <small>请先选择邮箱。</small>}
          {draft.id && (
            <button
              type="button"
              onClick={() =>
                setDraft({ id: "", title: "", start: "", end: "" })
              }
            >
              取消编辑
            </button>
          )}
        </form>
      </section>
      {items.length === 0 && (
        <section className="panel empty">当前范围暂无已生效日程。</section>
      )}
      {items.map((item) => (
        <section className="panel" key={item.id}>
          <h3>{item.body.title}</h3>
          <p>
            {new Date(item.body.start).toLocaleString("zh-CN")} —{" "}
            {new Date(item.body.end).toLocaleString("zh-CN")} ·{" "}
            {statusName(item.status)}
          </p>
          <div className="actions">
            <button
              disabled={!account}
              onClick={() =>
                setDraft({
                  id: item.id,
                  title: item.body.title,
                  start: localTime(item.body.start),
                  end: localTime(item.body.end),
                })
              }
            >
              编辑日程
            </button>
            {item.body.run_id && (
              <button onClick={() => onTrace(item.body.run_id)}>
                查看来源 Trace
              </button>
            )}
          </div>
        </section>
      ))}
    </>
  );
}
