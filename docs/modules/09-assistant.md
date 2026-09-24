# 邮件 Agent

## 功能与使用

先明确勾选本轮可读的邮箱，再输入问题，例如“导师最近要求修改哪些实验，有什么截止时间？”可以新建会话或恢复历史会话，查看回答、来源引用、工具调用步骤和 Agent Trace。Agent 能检索邮件、展开线程、读取已解析附件、查历史交互和待办，提出本地动作或生成草稿；它不能直接发信。证据不足时应澄清，引用可跳回原邮件。

## 实现链路

前端 [`AssistantPage.tsx`](../../frontend/src/mail/AssistantPage.tsx) 调用 `/assistant/sessions`、`/assistant/sessions/{id}/turns`，Worker 处理持久 `assistant` 任务。核心 [`mail_assistant.py`](../../backend/mail_assistant.py) 的 `run_turn()` 最多进行 6 轮决策、3 次检索、约 24,000 输入 Token 预算；每步先保存状态，再调用受控工具，记录审计与证据。会话创建时冻结账号范围、记忆和版本；检索与每个工具都由服务端再次校验账号，不能只相信模型遵守提示词。

`draft` 工具写本地草稿；`actions` 工具只能提出允许的本地动作，再交给 [`runtime.py`](../../backend/runtime.py) 的审批/执行链。模型调用统一走 [`planner.py`](../../backend/planner.py) 的预算、用量、计价和 Trace。演示模式返回规则性流程，真实模式才调用配置的模型；选中的邮件证据会发送给该模型服务。设计详见 [RAG 与分层记忆](../MAIL_RAG_MEMORY.md)。

## 调试提示

看到回答但没有待办时看工具步骤中是否出现 `actions`、Run 是否仍在排队或审批中；看到错误引用时对照 `citations` 与 `evidence`。检索结果降级与 Agent 回答质量需要分别评价。测试入口见 [`tests/test_mail.py`](../../tests/test_mail.py) 及浏览器测试 [`frontend/e2e/`](../../frontend/e2e)。
