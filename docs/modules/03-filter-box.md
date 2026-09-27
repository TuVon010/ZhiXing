# 过滤箱

## 功能与使用

“垃圾与过滤”统一显示待人工复核的 `review` 邮件和已经隔离的 `filtered` 邮件。待复核邮件可以放行或确认垃圾；已过滤邮件可以查看证据并恢复。它是**本地可恢复的视图**，不是远端邮箱的“垃圾邮件”文件夹，也不会删除远端邮件。

## 实现链路

[`MailboxPage.tsx`](../../frontend/src/features/inbox/MailboxPage.tsx) 复用阅读组件，根据页面状态查询 `GET /mail/messages?status=filterbox`。IMAP 收取时先由 [`filtering.py`](../../backend/app/modules/mail/filtering.py) 和 [`ingestion.py`](../../backend/app/modules/mail/ingestion.py) 应用账号规则，白名单优先，记录过滤原因；默认待复核和过滤邮件都不进入 Agent 上下文和知识检索。AI 感知的高垃圾评分只有在较高自评置信度、没有白名单保护，也没有明确待办、日程或回复需求等保护信号时，才会把当前本地邮件转为 `filtered`；分数不是校准概率。

恢复走 `POST /mail/messages/{id}/state`，纠偏走 `POST /mail/messages/{id}/perception/feedback`。恢复后重新排队建索引。纠偏默认只覆盖当前邮件；只有用户勾选“提炼为长期偏好候选”才会生成候选，并仍需在记忆页确认。相关实现见 [`perception.py`](../../backend/app/modules/mail/perception.py) 与 [`retrieval.py`](../../backend/app/modules/mail/retrieval.py)。

## 调试提示

区分“规则入库即过滤”和“先分析后 AI 过滤”：前者通常没有感知 Trace；后者有模型调用和 `perception`。恢复后检查索引任务是否完成，再验证搜索是否重新可见。
