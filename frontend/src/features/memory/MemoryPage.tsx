import { useWorkspace } from "../../app/workspace/context";
import { label } from "../../shared/mail";
export function MemoryPage() {
  const {
    page,
    account,
    memories,
    memory,
    setMemory,
    globalMemory,
    setGlobalMemory,
    act,
    api,
  } = useWorkspace();
  return (
    <>
      {page === "memory" && (
        <>
          <section className="panel">
            <h3>确认后进入后续运行的长期记忆</h3>
            <textarea
              aria-label="记忆内容"
              value={memory}
              onChange={(e) => setMemory(e.target.value)}
              placeholder="例如：回复导师时先给结论，再列出需要确认的问题。"
            />
            <label>
              <input
                type="checkbox"
                checked={globalMemory}
                onChange={(e) => setGlobalMemory(e.target.checked)}
              />
              明确共享到全部邮箱的通用偏好
            </label>
            <button
              disabled={!memory || (!globalMemory && !account)}
              onClick={() =>
                void act(async () => {
                  await api("mail/memory", {
                    content: memory,
                    account_id: globalMemory ? "global" : account,
                    memory_type: "preference",
                  });
                  setMemory("");
                }, "已创建待确认记忆")
              }
            >
              创建记忆候选
            </button>
          </section>
          {memories.map((r) => (
            <section className="panel" key={r.id}>
              <p>{r.body.content}</p>
              <small>
                {r.scope === "global" ? "全局共享" : "仅当前邮箱"} · {r.status}
              </small>
              {r.status === "candidate" ? (
                <>
                  <button
                    onClick={() =>
                      void act(() => api(`review/${r.id}/publish`, {}))
                    }
                  >
                    确认记忆
                  </button>
                  <button
                    onClick={() =>
                      void act(() => api(`review/${r.id}/reject`, {}))
                    }
                  >
                    拒绝
                  </button>
                </>
              ) : (
                r.status === "published" && (
                  <button
                    onClick={() =>
                      void act(() => api(`review/${r.id}/suspend`, {}))
                    }
                  >
                    撤销记忆
                  </button>
                )
              )}
              {r.status !== "published" && <button onClick={() => void act(() => api(`mail/records/${r.id}/trash`, {}), "记忆已移入回收站")}>移入回收站</button>}
            </section>
          ))}
        </>
      )}
    </>
  );
}
