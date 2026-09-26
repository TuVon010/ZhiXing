# Trace 与运行

## 功能与使用

按所选账号分页查看 Agent 轮次和动作运行的状态，三秒刷新一次。会话回答结束后，动作仍可能排队或等审批；这里可以进入 Trace 核对真实进度。已结束的运行可从列表隐藏，**隐藏不是删除**，审计和 Trace 仍保留。

## 实现链路

页面使用 [`MailActivity.tsx`](../../frontend/src/features/activity/MailActivity.tsx)；`GET /mail/activity` 由 [`mail.py`](../../backend/app/observability/mail.py) 聚合 `assistant_turn` 和 `run`，按账号范围分页并排除 `ui_hidden`。`GET /mail/traces/{id}` 可从 Agent 轮次追到关联运行、模型调用、工具审计和费用摘要；[`MailTrace.tsx`](../../frontend/src/features/activity/MailTrace.tsx) 展示时间线和 JSON 导出。隐藏接口为 `POST /mail/records/{id}/hide`，恢复显示为 `unhide`。

邮件逐封分析的 Trace 从**收件箱邮件详情**打开；它以 `mail_message` 为根，包含后台任务和模型调用。运行记录主要展示会话轮次和动作 Run，所以这里看不到每一封被动感知的邮件，不等于感知 Trace 丢失。Trace 聚合测试见 [`tests/test_tracing.py`](../../tests/test_tracing.py)。

## 调试提示

区分 `assistant_turn`（思考/工具步骤）、`run`（动作执行）、`mail_message`（逐封感知）三类根对象。导出 Trace 前可检查是否含真实邮件正文或模型输入；私人导出文件不要提交公开仓库。
