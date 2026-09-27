# 邮件智能检索助手

## 功能与使用

先勾选会话可读的邮箱，再输入问题，例如“导师最近要求修改哪些实验，有什么截止时间？”。“整理回答”会结合证据继续推理和调用工具；“只找原文”只返回邮件或附件片段，适合核对召回效果。两种模式都保存在当前会话中，按时间顺序显示。会话建成后邮箱范围固定；切换历史会话只显示该会话的结果。历史会话可重命名或移入回收站，在“记录管理 → 检索会话 → 查看回收站”恢复；运行中的会话不能删除。回答依据合并重复片段，可点击回到原邮件，也可单独打开处理过程 Trace。Agent 能检索邮件、展开线程、读取已解析附件、查历史交互和待办，提出本地动作或生成草稿；它不能直接发信。证据不足时应澄清。

## 实现链路

前端 [`AssistantPage.tsx`](../../frontend/src/features/assistant/AssistantPage.tsx) 调用 `/assistant/sessions`、`/assistant/sessions/{id}/turns`、`/assistant/sessions/{id}/rename`；只查原文也是一种持久会话轮次。删除使用记录管理的软删除接口。Worker 处理 `assistant` 任务。核心 [`assistant.py`](../../backend/app/modules/mail/assistant.py) 的 `run_turn()` 最多进行 6 轮决策、3 次检索、累计 24,000 输入 Token 预算；模型调用前使用近似 Token 估算预检，调用后以服务商实际用量为准。此前把 UTF-8 字节数直接与 Token 上限比较，会使中文证据较多的正常问题过早停止。现在后续轮次只传递压缩的证据与工具摘要，完整证据和步骤仍保存在记录及 Trace 中。达到预算后保留已找到的邮件线索供用户查阅。模型给出的时间筛选格式错误时会修正或忽略并记录提示，不让一次错误参数耗掉整轮检索。每轮冻结账号范围、记忆和版本；检索与每个工具都由服务端再次校验账号。混合检索由 [`retrieval.py`](../../backend/app/modules/mail/retrieval.py) 实现，关键词、向量和重排结果合并后按内容与线程去重，并限制单封邮件的片段数量。

`draft` 工具写本地草稿；`actions` 工具只能提出允许的本地动作，再交给 [`graph.py`](../../backend/app/agent/graph.py) 的审批/执行链。模型调用统一走 [`model_client.py`](../../backend/app/agent/model_client.py) 的预算、用量、计价和 Trace。演示模式返回规则性流程，真实模式才调用配置的模型；选中的邮件证据会发送给该模型服务。设计详见 [RAG 与分层记忆](../MAIL_RAG_MEMORY.md)。

## 调试提示

看到回答但没有待办时看工具步骤中是否出现 `actions`、Run 是否仍在排队或审批中；看到错误引用时对照 `citations` 与 `evidence`。检索结果降级与 Agent 回答质量需要分别评价。测试入口见 [`tests/test_mail.py`](../../tests/test_mail.py) 及浏览器测试 [`frontend/e2e/`](../../frontend/e2e)。
