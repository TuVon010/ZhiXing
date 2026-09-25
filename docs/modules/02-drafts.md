# 回复草稿

## 功能与使用

支持新邮件、回复和回复全部。草稿固定发件账号，展示收件人、抄送、主题、正文与版本；回复全部会排除本账号并去除重复地址。用户可编辑并保存，再点击“准备发送”，在同一页完成最终核对。草稿仅保存在本地，不会因生成或保存而发出。确认后的执行结果通过“查看发送结果”进入 Trace。

## 实现链路

[`DraftsPage.tsx`](../../frontend/src/mail/DraftsPage.tsx) 调用 `GET/POST /mail/drafts`、`POST /mail/drafts/{id}` 和 `POST /mail/drafts/{id}/send`。草稿构造与回复头处理在 [`mail_assistant.py`](../../backend/mail_assistant.py) 的 `draft()`；最终确认时冻结账号、收件人、抄送、正文、回复头、版本与哈希，并写入只能由服务端创建的 `authorization` 记录。草稿进入 `mail_draft`，动作运行进入 `run`，结果写执行账本。

真正发送在 [`mail_send.py`](../../backend/mail_send.py)，执行前 `runtime.py` 先核对服务端授权，再由 `check_draft()` 复查账号、版本、状态和冻结参数。修改任一发送字段会使旧确认失效。SMTP 返回不明时标记待核对，执行账本阻止自动重发；`smtp_accepted` 仅表示 SMTP 服务器接受。测试账号走模拟发送分支，不能打开真实 SMTP。实现和故障测试可从 [`tests/test_mail.py`](../../tests/test_mail.py) 的草稿版本、重复提交及 SMTP 不明用例开始。

## 调试提示

无法提交时先看草稿是否为 `draft`、界面是否有未保存修改、版本是否匹配；审批完成但未发送时查看 Run Trace 和账本，不直接重试外部动作。系统目前不支持发送附件。
