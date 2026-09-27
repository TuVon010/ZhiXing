# 归档箱与回收站

## 状态语义

`active` 是仍需处理的收件箱邮件，`archived` 是已经完成但仍可检索的邮件，`review` 与 `filtered` 不进入 Agent 检索，`trashed` 是本地回收站。四类视图互斥，均不移动或删除远端邮箱中的邮件。

## 操作闭环

收件箱中的“完成处理”把邮件转为 `archived`；归档箱可以恢复到收件箱。任意正常或归档邮件可通过“更多”移入回收站，`POST /mail/records/{id}/trash` 会保存 `_record_previous_status`，回收站的“恢复邮件”调用 `/restore` 回到原位置。恢复到 `active` 或 `archived` 时会重新排队建立索引。

查询由 [`repositories/messages.py`](../../backend/app/modules/mail/repositories/messages.py) 实现：收件箱严格限制为 `active`，`filterbox` 合并 `review` 与 `filtered`，其他状态分别查询。前端共享 [`MailboxPage.tsx`](../../frontend/src/features/inbox/MailboxPage.tsx)，只根据状态改变说明和允许操作。回归见 [`tests/test_mail_mailboxes.py`](../../tests/test_mail_mailboxes.py) 和 [`frontend/e2e/mailboxes.spec.ts`](../../frontend/e2e/mailboxes.spec.ts)。
