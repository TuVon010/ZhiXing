export type Api = (path: string, body?: unknown) => Promise<any>;
export type Row = {
  id: string;
  kind?: string;
  status: string;
  scope: string;
  body: Record<string, any>;
};
export const label: Record<string, string> = {
  active: "待处理",
  filtered: "已过滤",
  review: "待复核",
  archived: "本地归档",
  legacy: "历史资料",
  draft: "草稿",
  approval: "待审批",
  approved: "已批准",
  rejected: "已拒绝",
  simulated: "演示发送（未发信）",
  smtp_accepted: "SMTP 已接受",
  unknown: "发送待核对",
  running: "处理中",
  queued: "排队中",
  completed: "完成",
  paused: "暂停",
  failed: "失败",
  cancelled: "取消",
  budget_exceeded: "达到预算",
  clarification: "需要澄清",
};
export const actionLabel: Record<string, string> = {
  send_email: "发送邮件",
  update_todo: "更新待办",
  create_calendar: "创建日程",
  update_calendar: "修改日程",
  delete_item: "删除内容",
};
export const blankAccount = {
  name: "",
  address: "",
  provider: "qq",
  username: "",
  password: "",
  imap_host: "",
  imap_port: 993,
  imap_tls: "ssl",
  smtp_host: "",
  smtp_port: 465,
  smtp_tls: "ssl",
  enabled: false,
  auto_analyze: false,
  scan_limit: 20,
  hourly_analysis_limit: 20,
};
export const pretty = (x: any) => JSON.stringify(x, null, 2);
