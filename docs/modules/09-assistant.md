# 邮件智能检索助手

## 功能与使用

先勾选会话可读的邮箱，再输入问题，例如“导师最近要求修改哪些实验，有什么截止时间？”。“整理回答”会结合证据继续推理和调用工具；“只找原文”只返回邮件或附件片段，适合核对召回效果。两种模式都保存在当前会话中，按时间顺序显示。会话建成后邮箱范围固定；切换历史会话只显示该会话的结果。回答依据合并重复片段，可点击回到原邮件，也可单独打开处理过程 Trace。Agent 能检索邮件、展开线程、读取已解析附件、查历史交互和待办，提出本地动作或生成草稿；它不能直接发信。证据不足时应澄清。

## 实现链路

前端 [`AssistantPage.tsx`](../../frontend/src/features/assistant/AssistantPage.tsx) 调用 `/assistant/sessions`、`/assistant/sessions/{id}/turns`；只查原文也是一种持久会话轮次。Worker 处理 `assistant` 任务。核心 [`assistant.py`](../../backend/app/modules/mail/assistant.py) 的 `run_turn()` 最多进行 6 轮决策、3 次检索、约 24,000 输入 Token 预算；步骤、审计与证据随轮次保存。每轮冻结账号范围、记忆和版本；检索与每个工具都由服务端再次校验账号。混合检索由 [`retrieval.py`](../../backend/app/modules/mail/retrieval.py) 实现，关键词、向量和重排结果合并后按内容与线程去重，并限制单封邮件的片段数量。

`draft` 工具写本地草稿；`actions` 工具只能提出允许的本地动作，再交给 [`graph.py`](../../backend/app/agent/graph.py) 的审批/执行链。模型调用统一走 [`model_client.py`](../../backend/app/agent/model_client.py) 的预算、用量、计价和 Trace。演示模式返回规则性流程，真实模式才调用配置的模型；选中的邮件证据会发送给该模型服务。设计详见 [RAG 与分层记忆](../MAIL_RAG_MEMORY.md)。

## 调试提示

看到回答但没有待办时看工具步骤中是否出现 `actions`、Run 是否仍在排队或审批中；看到错误引用时对照 `citations` 与 `evidence`。检索结果降级与 Agent 回答质量需要分别评价。测试入口见 [`tests/test_mail.py`](../../tests/test_mail.py) 及浏览器测试 [`frontend/e2e/`](../../frontend/e2e)。
