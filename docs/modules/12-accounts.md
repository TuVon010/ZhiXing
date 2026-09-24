# 邮箱账号

## 功能与使用

添加多个 QQ、163 或通用 IMAP/SMTP 账号，配置名称、地址、登录名、授权码/密码、TLS 连接、扫描上限和自动分析等选项。保存后先“测试连接”，再明确启用收取；测试连接会检查 IMAP/SMTP 登录但不发送邮件。可手动“同步一轮”及暂停收取。页面还可幂等创建 14 封合成邮件的 Agent 测试邮箱，地址为 `.invalid`，无凭证、无真实收发。

## 实现链路

[`AccountsPage.tsx`](../../frontend/src/mail/AccountsPage.tsx) 对应 `/mail/accounts`、`/{id}/test`、`/{id}/enabled`、`/{id}/sync` 和 `/mail/test-scenarios`。请求模型是 [`mail_models.py`](../../backend/mail_models.py) 的 `MailAccount`；[`mail_store.py`](../../backend/mail_store.py) 保存账号和 Windows 当前用户 DPAPI 加密凭证，普通查询只返回“已配置”标志，不回显密钥。[`mail_ingest.py`](../../backend/mail_ingest.py) 建立受 TLS 保护的 IMAP 连接；[`mail_worker.py`](../../backend/mail_worker.py) 每账号持久排队。合成邮箱由 [`mail_scenarios.py`](../../backend/mail_scenarios.py) 固定 ID 和 UID 建立，远端连接/导入被服务端拦截。

账号范围是安全边界：线程、搜索、附件、草稿、工具调用都应在服务端验证归属。设置“全部邮箱”只改变浏览视图，不自动授权 Agent 跨账号检索。凭证备份跨 Windows 用户或跨机器恢复时需重新输入，详见 [邮箱配置](../MAIL_SETUP.md)。

## 调试提示

认证失败先看账号的最近任务错误、TLS/端口、服务商授权码及是否开启 IMAP/SMTP；不要把密钥贴进日志或公开 issue。单账号失败不应阻塞其他邮箱。验证账号隔离可用三个合成账号同 UID 测试，见 [`tests/test_mail.py`](../../tests/test_mail.py)。
