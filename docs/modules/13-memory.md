# 记忆

## 功能与使用

记录长期偏好，例如“回复导师先给结论”。新内容先成为**记忆候选**，需要用户确认才进入后续运行；也可拒绝或撤销已发布记忆。默认仅作用于当前邮箱，只有勾选“明确共享到全部邮箱”才创建全局偏好。邮件里第三方说的话不会直接提升为用户偏好。

## 实现链路

[`MemoryPage.tsx`](../../frontend/src/mail/MemoryPage.tsx) 调 `POST /mail/memory` 创建 `memory` 候选，`POST /review/{id}/{publish|reject|suspend}` 处理状态。感知纠偏由 [`mail_perception.py`](../../backend/mail_perception.py) 的 `_learn_from_feedback()` 产生候选。Agent 创建会话时 [`mail_assistant.py`](../../backend/mail_assistant.py) 的 `memory_snapshot()` 读取已发布、在账号范围内的记忆并冻结到运行快照；之后修改记忆不回写已开始的运行。感知也只读取发布状态的偏好并限制上下文长度。

数据库是主存储；可读记忆快照与知识索引属于不同层。记忆用于影响后续处理方式，邮件知识库用于查具体事实，短期线程上下文用于当前会话。详细设计见 [RAG 与分层记忆](../MAIL_RAG_MEMORY.md)。

## 调试提示

偏好“没有生效”时核对其 `status` 是否 `published`、`scope` 是否为所选邮箱或 `global`，以及 Agent 会话是否在发布前创建。撤销只影响新运行；已有 Trace 保留当时冻结版本，便于复现。
