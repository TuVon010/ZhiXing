# 记录管理

## 功能与使用

盘点新版邮件、草稿、待办、日程、提醒、通知、记忆、Agent 会话及动作运行；按账号、类型、回收站状态分页。可把适用记录移入**本地**回收站并恢复，或隐藏已结束的运行。旧项目来源记录单独列出，先生成备份再人工核查；仅凭旧 `email` 来源字段无法判断是真实邮件还是测试样本，因此不会自动删除。这里不会删除邮箱服务器上的邮件。

## 实现链路

[`RecordsPage.tsx`](../../frontend/src/mail/RecordsPage.tsx) 调 `/mail/records/overview`、`/list`、`/{id}/trash`、`/{id}/restore`、`/{id}/hide`、`/{id}/unhide` 和 `/legacy/backup`，路由与限制在 [`mail_records.py`](../../backend/mail_records.py)。邮件移入回收站后不应继续出现在检索上下文；恢复活动邮件会重排索引。未提交草稿可回收，已审批或已发送草稿保留审计；已发布记忆要先撤销；活动待办、日程、提醒的删除仍生成审批动作。运行只隐藏 UI，不删 Trace 或审计。

旧资料备份使用 SQLite 在线快照，同时备份业务库和 checkpoint；它不等于跨机器完整恢复包，凭证的 DPAPI 限制仍在。常规整库备份与恢复另见 [迁移与恢复](../MAIL_MIGRATION.md)。记录管理测试见 [`tests/test_mail_records.py`](../../tests/test_mail_records.py)。

## 调试提示

本地待办、日程和提醒执行“移入回收站”后立即成为 `trashed`，可在记录管理中恢复；日程或待办移入回收站时会停用关联提醒，恢复时重建提醒。区分 `trashed`（可恢复本地记录）、`deleted`（历史动作删除）与 `ui_hidden`（仅不在运行列表展示）。测试归档 `artifacts/test-runs/` 不属于主库记录，不在此页批量清理。
