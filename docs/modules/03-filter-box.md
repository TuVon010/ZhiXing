# 过滤箱

## 功能与使用

显示被本地规则或高分 AI 判断隔离的邮件。可打开原文、查看过滤理由和感知结果，纠正分类或恢复处理。它是**本地可恢复的视图**，不是远端邮箱的“垃圾邮件”文件夹，也不会删除远端邮件。需要调规则到“设置与计价”。

## 实现链路

[`MailboxPage.tsx`](../../frontend/src/mail/MailboxPage.tsx) 复用阅读组件，根据页面状态查询 `GET /mail/messages?status=filtered`。IMAP 收取时先由 [`filtering.py`](../../backend/filtering.py) 和 [`mail_ingest.py`](../../backend/mail_ingest.py) 应用账号规则，白名单优先，记录过滤原因；默认过滤邮件不进入 Agent 上下文和知识检索。AI 感知的高垃圾评分只有在较高自评置信度、没有白名单保护，也没有明确待办、日程或回复需求等保护信号时，才会把当前本地邮件转为 `filtered`；分数不是校准概率。

恢复走 `POST /mail/messages/{id}/state`，纠偏走 `POST /mail/messages/{id}/perception/feedback`。恢复后重新排队建索引；纠偏还会产生**待确认**的偏好记忆，不会直接污染后续模型上下文。被过滤状态会使检索和 Agent 证据过滤失效。相关实现见 [`mail_perception.py`](../../backend/mail_perception.py) 与 [`mail_rag.py`](../../backend/mail_rag.py)；规则回归见 [`tests/test_filtering.py`](../../tests/test_filtering.py)（若定位测试时以 `tests/` 中实际文件为准）。

## 调试提示

区分“规则入库即过滤”和“先分析后 AI 过滤”：前者通常没有感知 Trace；后者有模型调用和 `perception`。恢复后检查索引任务是否完成，再验证搜索是否重新可见。
