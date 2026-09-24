# 审批中心

## 功能与使用

列出当前范围内等待人工决策的动作，尤其是外发邮件。页面展开账号、收件人、抄送、主题、**完整正文**及版本；可批准、拒绝并打开执行 Trace。审批按钮表示同意执行当前冻结参数，不代表动作已成功，更不代表对方已收到邮件。编辑草稿需要回草稿页保存，随后重新申请审批。

## 实现链路

[`ApprovalsPage.tsx`](../../frontend/src/mail/ApprovalsPage.tsx) 使用 `GET /mail/approvals` 和 `POST /approvals/{id}`。草稿提交创建 `run`/`approval`；[`runtime.py`](../../backend/runtime.py) 用 LangGraph checkpoint 暂停，审批后恢复。动作执行前 [`tools.py`](../../backend/tools.py) 与 [`mail_send.py`](../../backend/mail_send.py) 复查版本、账号和参数，并通过 `ledger` 防止重复外部写入。重复点击只应有一次生效；结果不明进入待核对，不自动重发。

中风险日程修改等动作也可能出现在此处。自动本地待办、完成待办、关闭邮件跟进不需要审批；高风险发送不能由模型“自我批准”。审批设计详见 [工作项与审批边界](../MAIL_WORK_AUTOMATION.md)，恢复与幂等测试见 [`tests/test_runtime.py`](../../tests/test_runtime.py) 及 [`tests/test_mail.py`](../../tests/test_mail.py)。

## 调试提示

碰到“批准后没动静”，按 `approval_id → run_id → action_id → ledger → Trace` 查；`waiting_approval`、`queued`、`completed` 是不同状态。草稿版本变化后旧审批失效属于预期保护。
