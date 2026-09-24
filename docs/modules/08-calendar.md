# 日程

## 功能与使用

查看本地日程记录及其状态，手动新建或编辑；选择账号后输入标题、开始及结束时间。列表目前也会显示候选、已忽略或已取消记录，操作前要看状态；候选的确认/忽略入口在“待办与跟进”。手工创建/修改提交为动作运行，页面会提示去审批中心；邮件感知产生的精确低风险日程可自动保存，冲突、缺时间或同线程改期进入“待核实建议”。日程不会自动同步到远端日历，也不邀请他人。

## 实现链路

[`MailCalendar.tsx`](../../frontend/src/MailCalendar.tsx) 从 `GET /mail/followups` 读取 `calendar`，向 `POST /mail/calendar` 或 `POST /mail/calendar/{id}` 提交手工动作。后端 [`mail_observability.py`](../../backend/mail_observability.py) 验证时间带时区、结束晚于开始，经 [`runtime.py`](../../backend/runtime.py) 进入审批执行。邮件自动提取则由 [`mail_perception.py`](../../backend/mail_perception.py) 提案、[`mail_work_items.py`](../../backend/mail_work_items.py) 校验，再由 [`mail_schedule.py`](../../backend/mail_schedule.py) 做幂等、冲突和改期检查。

同线程已有不同时间的活动日程时，新项先作为候选；用户确认改期后旧日程与旧提醒停用，原记录仍可追溯。正常确认会生成提前约 30 分钟的本地提醒。参会型 Todo 不再重复出现。测试见 [`tests/test_mail_work_items.py`](../../tests/test_mail_work_items.py) 和 [`tests/test_new_features.py`](../../tests/test_new_features.py)。

## 调试提示

比较 `start/end`、`source_message`、`has_conflict`、`prior_thread_events` 与提醒的 `source_calendar`。注意日期按本机时区输入，数据库保存带时区 ISO 时间。
