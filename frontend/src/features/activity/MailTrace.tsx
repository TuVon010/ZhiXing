import { BillingSummary } from "../settings/PricingSettings";

export type RecordRow = {
  id: string;
  kind: string;
  status: string;
  scope?: string;
  created_at: string;
  body: Record<string, any>;
};
export type TraceData = {
  id: string;
  root: RecordRow;
  runs: { run: RecordRow; trace: unknown }[];
  audit: RecordRow[];
  model_calls: RecordRow[];
  billing: any;
  jobs?: { id: string; kind: string; status: string; result: unknown; attempts: number }[];
};
export const statusName = (value: string) =>
  ({
    queued: "排队中",
    running: "执行中",
    waiting_approval: "等待审批",
    completed: "已完成",
    completed_with_attention: "需要处理",
    failed: "失败",
    cancelled: "已取消",
    budget_exceeded: "达到预算",
    clarification: "待澄清",
    active: "待处理",
    read: "已读",
    local: "未读",
    pending: "未读",
  })[value] || value;
const pretty = (value: unknown) => JSON.stringify(value, null, 2);
const events: Record<string, string> = {
  MESSAGE_RECEIVED: "收到请求",
  UNDERSTAND_STARTED: "开始理解",
  ACTION_PLANNED: "生成动作计划",
  RISK_CHECKED: "风险判断",
  APPROVAL_REQUESTED: "等待人工审批",
  APPROVAL_DECIDED: "审批已决定",
  USER_CONFIRMATION_APPLIED: "应用用户最终发送确认",
  TOOL_CALLED: "执行工具",
  TOOL_RESULT: "工具执行结果",
  WORKFLOW_FINISHED: "动作运行结束",
  WORKFLOW_FAILED: "动作运行失败",
  WORKFLOW_PAUSED: "等待审批",
  WORKFLOW_RESUMED: "恢复执行",
  MAIL_AGENT_STEP: "Agent 决策与工具结果",
  MAIL_RETRIEVAL: "检索证据",
  MODEL_REQUEST: "请求模型",
  MODEL_RESPONSE: "模型返回",
  MODEL_FAILED: "模型调用失败",
};
const date = (value: string) =>
  new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" });

export function MailTrace({
  data,
  onClose,
  onRefresh,
}: {
  data: TraceData;
  onClose: () => void;
  onRefresh: () => void;
}) {
  const download = () => {
    const url = URL.createObjectURL(
      new Blob([pretty(data)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `zhixing-trace-${data.id}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return (
    <div className="modal" role="dialog" aria-label="运行 Trace">
      <section className="panel mail-trace">
        <div className="mail-sectionbar">
          <h2>{data.root.kind === "mail_message" ? "邮件感知 Trace" : "运行 Trace"}</h2>
          <div className="actions">
            <button onClick={onRefresh}>刷新执行结果</button>
            <button onClick={onClose}>关闭</button>
          </div>
        </div>
        <p>
          {data.root.body.text ||
            data.root.body.summary ||
            data.root.body.message?.text}
        </p>
        <p>
          {data.root.kind === "mail_message" ? "邮件状态" : "Agent / 主运行"}：{statusName(data.root.status)} ·{" "}
          {date(data.root.created_at)}
        </p>
        <small>
          Trace ID：{data.id}。对话完成与动作执行分别展示；批准不等于执行成功。
        </small>
        <BillingSummary value={data.billing} />
        {data.jobs && <>
          <h3>后台任务</h3>
          {data.jobs.map((job) => <details key={job.id}>
            <summary>{job.kind} · {statusName(job.status)} · 尝试 {job.attempts} 次</summary>
            <pre>{pretty(job.result)}</pre>
          </details>)}
        </>}
        <h3>动作执行结果</h3>
        {!data.runs.length && data.root.kind !== "mail_message" && <p>本轮尚未提交动作运行。</p>}
        {data.runs.map(({ run }) => (
          <article className="mail-evidence" key={run.id}>
            <strong>
              {run.body.summary || run.body.message?.text || run.id} ·{" "}
              {statusName(run.status)}
            </strong>
            {Object.entries(run.body.outcomes || {}).map(([id, value]) => (
              <div key={id}>
                <small>动作 {id}</small>
                <details>
                  <summary>动作结果详情</summary>
                  <pre>{pretty(value)}</pre>
                </details>
              </div>
            ))}
            {!Object.keys(run.body.outcomes || {}).length && (
              <p>暂无执行结果，可能正在排队或等待审批。</p>
            )}
          </article>
        ))}
        <h3>执行时间线</h3>
        <ol className="trace-timeline">
          {data.audit.map((event) => (
            <li key={event.id}>
              <strong>
                {events[event.body.event_type] ||
                  event.body.event_type ||
                  "事件"}
              </strong>{" "}
              <small>{date(event.created_at)}</small>
              <details>
                <summary>输入、输出与关联 ID</summary>
                <pre>{pretty(event.body)}</pre>
              </details>
            </li>
          ))}
        </ol>
        <h3>模型调用（{data.model_calls.length}）</h3>
        {data.model_calls.map((call) => (
          <details key={call.id}>
            <summary>
              {call.body.model || call.kind} · {statusName(call.status)} ·{" "}
              {call.body.latency_ms ?? "未知"} ms
            </summary>
            <pre>{pretty(call.body)}</pre>
          </details>
        ))}
        <button onClick={download}>导出邮件 Trace JSON</button>
        <details>
          <summary>完整原始记录与版本快照</summary>
          <pre>{pretty(data)}</pre>
        </details>
      </section>
    </div>
  );
}
