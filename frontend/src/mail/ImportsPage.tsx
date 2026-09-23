import { useWorkspace } from "./context";
import { pretty, label } from "./shared";
export function ImportsPage() {
  const {
    page,
    accounts,
    account,
    setNotice,
    busy,
    range,
    setRange,
    job,
    setJob,
    imports,
    current,
    act,
    waitJob,
    rangePayload,
    api,
  } = useWorkspace();
  return (
    <>
      {page === "imports" && (
        <>
          <section className="panel">
            <h3>历史导入 · {current?.body.name || "请先选择邮箱"}</h3>
            <p>
              起止时间按本机时间输入，结束时间不包含在范围内。预览不下载正文、不推进实时游标。
            </p>
            <div className="pricing-grid">
              <label>
                开始时间
                <input
                  aria-label="开始时间"
                  type="datetime-local"
                  value={range.start}
                  onChange={(e) =>
                    setRange({ ...range, start: e.target.value })
                  }
                />
              </label>
              <label>
                结束时间
                <input
                  aria-label="结束时间"
                  type="datetime-local"
                  value={range.end}
                  onChange={(e) => setRange({ ...range, end: e.target.value })}
                />
              </label>
              <label>
                本批累计上限
                <input
                  type="number"
                  min={1}
                  max={10000}
                  value={range.limit}
                  onChange={(e) =>
                    setRange({ ...range, limit: +e.target.value })
                  }
                />
              </label>
            </div>
            <div className="actions">
              <button
                disabled={busy}
                onClick={() =>
                  void act(async () => {
                    const result = await waitJob(
                      (await api("mail/imports/preview", rangePayload()))
                        .job_id,
                    );
                    setNotice("匹配邮件：" + result.matched + " 封");
                  }, "预览已完成，查看下方任务结果")
                }
              >
                预览范围
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  void act(
                    () => api("mail/imports", rangePayload()),
                    "导入批次已创建",
                  )
                }
              >
                创建导入批次
              </button>
              <button
                disabled={!account}
                onClick={() =>
                  void act(
                    async () =>
                      setJob(await api("mail/accounts/" + account + "/status")),
                    "",
                  )
                }
              >
                查看账号状态
              </button>
            </div>
            {job && <pre>{pretty(job)}</pre>}
            <details>
              <summary>离线积压处理</summary>
              <p>
                默认最多补读最近 24 小时的 50
                封。继续将放行当前剩余积压；从现在开始会跳过尚未收取的旧邮件，但不删除远端内容。
              </p>
              <button
                disabled={!account}
                onClick={() =>
                  void act(() =>
                    api(`mail/accounts/${account}/backlog/continue`, {}),
                  )
                }
              >
                继续当前积压
              </button>
              <button
                disabled={!account}
                onClick={() =>
                  void act(() =>
                    api(`mail/accounts/${account}/backlog/from_now`, {}),
                  )
                }
              >
                重新从现在开始
              </button>
            </details>
          </section>
          {imports.map((r) => (
            <section className="panel" key={r.id}>
              <h3>
                {label[r.status] || r.status} · {r.body.imported}/{r.body.limit}{" "}
                封
              </h3>
              <p>
                {r.body.start} — {r.body.end}
              </p>
              {["pause", "resume", "cancel"].map((v, i) => (
                <button
                  key={v}
                  disabled={["completed", "cancelled"].includes(r.status)}
                  onClick={() =>
                    void act(() => api(`mail/imports/${r.id}/${v}`, {}))
                  }
                >
                  {["暂停", "继续", "取消"][i]}
                </button>
              ))}
            </section>
          ))}
        </>
      )}
    </>
  );
}
