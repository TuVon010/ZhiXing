# 收件箱

## 功能与使用

按顶部邮箱选择器浏览邮件，“统一收件箱”默认汇总真实邮箱，测试账号由用户显式显示。列表支持今天、未读、需要行动、待分析和分类筛选，并可按智能重要度、服务器收件时间正序或倒序排列。点开邮件记录本地已读状态，可再次标记未读；列表同时显示摘要、优先级、待回复和索引状态。Agent 的读取范围必须另外显式勾选。

## 实现链路

- 页面在 [`MailboxPage.tsx`](../../frontend/src/features/inbox/MailboxPage.tsx)，共享邮箱选择、分页与打开邮件状态由 [`useMailWorkspace.ts`](../../frontend/src/app/workspace/useMailWorkspace.ts) 管理。
- `GET /mail/messages` 按账号、状态分页，`GET /mail/messages/{id}` 读取详情，`GET /mail/threads/{id}` 展开往来；路由位于 [`router.py`](../../backend/app/modules/mail/router.py)。服务端对邮件及线程做账号范围检查。
- IMAP 邮件先由 [`ingestion.py`](../../backend/app/modules/mail/ingestion.py) 解析头部、正文、附件元信息并关联线程；存储使用账号、文件夹、UIDVALIDITY、UID 去重，主题相同不会单独合并线程。原文保留，清理后的文本供索引；不执行邮件内容或远端图片。
- “立即分析 / 重新分析”提交持久任务，Worker 调用 [`perception.py`](../../backend/app/modules/mail/perception.py)；模型原始判断写入 `perception_model`，人工覆盖写入 `perception_overrides`，最终展示值保存在 `perception`。因此重新分析不会覆盖已经确认的人工纠正。感知 Trace 可从邮件详情打开。收取、索引、分析是三步，**入库并不等于已经有 Agent 产出**。
- 详情页根据感知结果选择主操作：需要回复时优先 AI 起草，有日程或待办时跳到对应工作页，普通邮件可以“完成处理”进入归档箱。多封往来或带附件时才显示“总结整段往来”，结果和证据直接留在当前详情页。

## 调试与边界

先看邮件详情的 `index_status` 和 `perception`，再到“收取与导入”核对排队/失败数量，最后看邮件感知 Trace 的 `jobs`、`model_calls`。收件箱只查询 `active`，待复核、归档和回收状态不会混回收件箱。`POST /mail/messages/{id}/state` 的归档和恢复只改本地状态；误过滤可恢复，不会改远端文件夹。HTML、PDF/DOCX/TXT 的可读范围受解析限制，扫描件没有 OCR。参考 [`tests/test_mail_mailboxes.py`](../../tests/test_mail_mailboxes.py) 与 [`tests/test_mail_perception.py`](../../tests/test_mail_perception.py)。
