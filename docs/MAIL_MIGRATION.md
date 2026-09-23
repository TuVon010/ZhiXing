# 邮件重构迁移与恢复

## 迁移

先停止服务。scripts/snapshot_mail_upgrade.py 保留源码、用户未提交修改、新文件、业务库、checkpoints、附件、文档、日志和本机配置。备份含隐私，不提交 Git。首份重构前备份为 backups/mail-upgrade-20260922-225558，后续不得覆盖。

运行 scripts/migrate_mail.py。migrations version=2 保证幂等。旧 .env 邮箱以暂停状态导入，仍兼容旧凭证引用，可在新页面改存 DPAPI。

只有可靠标识能确定账号才归属；否则进入 legacy-unassigned。缺头部、附件、准确收件时间标为 incomplete，不伪造恢复。旧任务、审批、审计和 LangGraph checkpoints 保留。

旧排队/运行/等待审批进入 migration_review，旧通知暂停。设置页人工选择恢复后继续原流程。旧邮件不自动分析，不自动执行积压发信。

## 恢复

1. 停止 API 与 Worker，确认项目不再监听 8000。
2. 当前数据与源码另存新故障目录。
3. 从同一备份成对恢复 zhixing.db、checkpoints.db、附件和配置，不能混用不同时间的库。
4. 将 source.zip 解压到独立恢复目录，不覆盖当前工作区；核对 manifest 的 HEAD 和 changes.patch。
5. 使用 SQLite 一致备份文件，不能附上另一运行的 WAL/SHM。
6. 跨 Windows 用户/机器重新录入凭证。

模型可按 manifest 重新下载，检索索引可重建；原邮件、审批、执行账本不可随意丢弃。恢复数据库不能撤回已发送邮件，SMTP 不明结果仍需人工核对。
