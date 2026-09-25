# 待办与跟进

## 功能与使用

页面把下一步工作分为四栏：**待办**是自己要完成的动作，可完成/重新打开；**邮件跟进**表示需要回复或已回复等待对方，可起草回复或标记处理；**待核实建议**收纳时间不明、证据不足或冲突的模型提案，可确认或忽略；**提醒**是由截止时间或日程派生的本地通知。顶部可看昨日邮件摘要，也可手动添加待办。每条自动事项都应能回到原邮件和感知 Trace。

## 实现链路

[`FollowupsPage.tsx`](../../frontend/src/mail/FollowupsPage.tsx) 从 `GET /mail/followups` 读取 `todo`、`mail_followup`、`calendar`、`reminder`。模型在 [`mail_perception.py`](../../backend/mail_perception.py) 生成结构化提案，服务端 [`mail_work_items.py`](../../backend/mail_work_items.py) 再核对引用原文、时间、分类、风险词和置信度。明确且低风险的本地待办自动 `active`；模糊项保留 `candidate`。回复确认不会重复变 Todo，参会优先归日程，截止时间优先归待办提醒。

本地完成/重新打开走 `POST /mail/todos/{id}/{operation}`；邮件跟进处理走 `POST /mail/followups/{id}/{operation}`；候选确认与忽略走 `/mail/perception/todos/...` 或 `/mail/perception/calendars/...`。这些本地可逆操作直接生效，删除进入本地回收站并可恢复。真正发送回复仍需在草稿页由用户最终确认。每日摘要见 [`mail_digest.py`](../../backend/mail_digest.py)，生成时复用已感知结果。

## 调试提示

看事项的 `source_message`、`source_quote`、`decision.reason` 与 `policy_version`，再比对原邮件。合成邮箱有明确、含糊、改期、广告和注入案例，适合观察自动/候选分流。验证见 [`tests/test_mail_work_items.py`](../../tests/test_mail_work_items.py) 和 [工作项规则说明](../MAIL_WORK_AUTOMATION.md)。
