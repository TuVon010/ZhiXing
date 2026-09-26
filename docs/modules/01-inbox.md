# 收件箱

## 功能与使用

按顶部邮箱选择器浏览邮件，“统一收件箱”默认汇总真实邮箱，测试账号由用户显式显示。列表支持今天、未读、需要行动、待分析和分类筛选，并可按智能重要度、服务器收件时间正序或倒序排列。点开邮件记录本地已读状态，可再次标记未读；列表同时显示摘要、优先级、待回复和索引状态。Agent 的读取范围必须另外显式勾选。

## 实现链路

- 页面在 [`MailboxPage.tsx`](../../frontend/src/features/inbox/MailboxPage.tsx)，共享邮箱选择、分页与打开邮件状态由 [`useMailWorkspace.ts`](../../frontend/src/app/workspace/useMailWorkspace.ts) 管理。
- `GET /mail/messages` 按账号、状态分页，`GET /mail/messages/{id}` 读取详情，`GET /mail/threads/{id}` 展开往来；路由位于 [`router.py`](../../backend/app/modules/mail/router.py)。服务端对邮件及线程做账号范围检查。
- IMAP 邮件先由 [`ingestion.py`](../../backend/app/modules/mail/ingestion.py) 解析头部、正文、附件元信息并关联线程；存储使用账号、文件夹、UIDVALIDITY、UID 去重，主题相同不会单独合并线程。原文保留，清理后的文本供索引；不执行邮件内容或远端图片。
- “分析 / 重新分析”提交持久任务，Worker 调用 [`perception.py`](../../backend/app/modules/mail/perception.py)；结果写入 `mail_message.body.perception`，感知 Trace 可从邮件详情打开。收取、索引、分析是三步，**入库并不等于已经有 Agent 产出**。

## 调试与边界

先看邮件详情的 `index_status` 和 `perception`，再到“收取与导入”核对排队/失败数量，最后看邮件感知 Trace 的 `jobs`、`model_calls`。`POST /mail/messages/{id}/state` 的归档和恢复只改本地状态；误过滤可恢复，不会改远端文件夹。HTML、PDF/DOCX/TXT 的可读范围受解析限制，扫描件没有 OCR。参考 [`tests/test_mail.py`](../../tests/test_mail.py) 与 [`tests/test_mail_perception_queue.py`](../../tests/test_mail_perception_queue.py)。
