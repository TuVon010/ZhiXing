# 通知与提醒

## 功能与使用

集中查看到期提醒、审批请求与执行结果，显示未读数量。可标记已读、查看关联运行 Trace，或把本地通知移入回收站。它目前是**Web 工作台内通知**，不是 Windows 桌面推送、手机推送或对外邮件通知。

## 实现链路

[`MailActivity.tsx`](../../frontend/src/features/activity/MailActivity.tsx) 在通知模式调用 `GET /mail/notifications`，并每三秒刷新；`POST /mail/notifications/{id}/read` 改已读状态。服务端查询在 [`mail.py`](../../backend/app/observability/mail.py)，账号过滤既覆盖直接属于该邮箱的提醒，也覆盖关联运行的通知。待办截止时间和日程开始时间会建立 `reminder`；后台调度由 Worker 处理，实际展示为本地 `notification` 记录。移入回收站走记录管理的 `trash` 接口，保留恢复可能。

## 调试提示

到期不显示时先核对 `reminder.due_at` 时区和状态，再查 Worker 心跳、通知记录与当前账号范围。草稿发送结果通知只反映执行状态，真正的发送授权来自草稿页的最终确认。
