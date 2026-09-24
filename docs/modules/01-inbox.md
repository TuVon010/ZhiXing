# 收件箱

## 功能与使用

按顶部邮箱选择器浏览邮件，列表显示发件人、主题、收件时间、索引状态，以及已分析邮件的一句话摘要、分类、优先级和待回复标签。点开邮件可读原文、附件状态和 AI 感知结果，并可查看同线程邮件。可从当前邮件创建回复/回复全部草稿、询问线程、手动分析、移入本地回收站或本地归档。顶部“全部邮箱”便于浏览；Agent 的读取范围必须另外显式勾选。

## 实现链路

- 页面在 [`MailboxPage.tsx`](../../frontend/src/mail/MailboxPage.tsx)，共享邮箱选择、分页与打开邮件状态由 [`useMailWorkspace.ts`](../../frontend/src/mail/useMailWorkspace.ts) 管理。
- `GET /mail/messages` 按账号、状态分页，`GET /mail/messages/{id}` 读取详情，`GET /mail/threads/{id}` 展开往来；路由位于 [`mail_api.py`](../../backend/mail_api.py)。服务端对邮件及线程做账号范围检查。
- IMAP 邮件先由 [`mail_ingest.py`](../../backend/mail_ingest.py) 解析头部、正文、附件元信息并关联线程；存储使用账号、文件夹、UIDVALIDITY、UID 去重，主题相同不会单独合并线程。原文保留，清理后的文本供索引；不执行邮件内容或远端图片。
- “分析 / 重新分析”提交持久任务，Worker 调用 [`mail_perception.py`](../../backend/mail_perception.py)；结果写入 `mail_message.body.perception`，感知 Trace 可从邮件详情打开。收取、索引、分析是三步，**入库并不等于已经有 Agent 产出**。

## 调试与边界

先看邮件详情的 `index_status` 和 `perception`，再到“收取与导入”核对排队/失败数量，最后看邮件感知 Trace 的 `jobs`、`model_calls`。`POST /mail/messages/{id}/state` 的归档和恢复只改本地状态；误过滤可恢复，不会改远端文件夹。HTML、PDF/DOCX/TXT 的可读范围受解析限制，扫描件没有 OCR。参考 [`tests/test_mail.py`](../../tests/test_mail.py) 与 [`tests/test_mail_perception_queue.py`](../../tests/test_mail_perception_queue.py)。
