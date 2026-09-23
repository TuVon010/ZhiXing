# 测试地图

| 文件 | 保护的功能 |
| --- | --- |
| test_mail.py | 多邮箱主要业务、作用域、草稿审批、导入和恢复 |
| test_mail_extended.py | 邮件/附件/检索的边界和故障案例 |
| test_mail_workspace.py | 统一 Trace、通知、日程、导入续批和队列公平 |
| test_billing.py | 仍在使用的计价、缓存与快照 |
| test_tracing.py | 仍在使用的模型调用与执行审计 |
| test_evolution.py | 可选 Skill/Parser 演进的安全与发布门槛 |
| test_runtime.py、test_channels.py | 审批恢复、账本和已停用直发路径的防回归 |
| test_model.py、test_api.py、test_filtering.py | 模型契约、API 安全、过滤 |
| test_evaluation_archive.py | 评测留档与拒绝覆盖历史结果 |

当前不把两个邮件测试文件机械拼成上千行大文件。后续新增测试优先按功能命名，如 workspace；确需再拆分时按收取、检索、草稿等行为拆分并提取公共 fixture。

所有外部邮箱与主模型测试默认合成/mock。运行 scripts/test.ps1 保存每轮命令、数据库、日志、JUnit 和浏览器证据，不能把模拟通过表述为真实接入成功。
