# 脚本入口

| 类型 | 脚本 | 何时使用 |
| --- | --- | --- |
| 日常 | install.ps1、env.ps1、start.ps1、stop.ps1、check.ps1 | 安装、进程环境、启停与诊断 |
| 测试/备份 | test.ps1、run_checks.py、backup.ps1/py | 留档回归与停服备份 |
| 模型/评测 | download_mail_models.py、evaluate_mail.py、rag_evaluation.py | 下载检索模型和复现实验 |
| 升级维护 | migrate_brand.py、migrate_mail.py、snapshot_mail_upgrade.py | 旧安装的显式迁移/升级快照，新安装无需日常执行 |
| 恢复验证 | verify_mail_recovery.py | 用隔离资料进行恢复演练，属于维护测试 |
| 开发辅助 | export_openapi.py、e2e_server.py、create_environment.py、demo.ps1 | 生成契约、测试服务、创建环境、演示 |

保留已有路径是为了兼容安装与恢复文档，不把迁移脚本放进日常启动链。具体业务学习从 backend/README.md 和 docs/WORKSPACE_GUIDE.md 开始。

`start.ps1` 默认最多等待 60 秒，确认本次启动的 API 进程监听 `127.0.0.1:8000`，且 `/api/health` 返回正常状态后才显示控制台地址。脚本会直接使用项目的 Conda Python，无需手动激活。启动超时先看 `logs/api.err.log`；如果日志显示仍在初始化，可以用 `.\scripts\start.ps1 -StartupTimeoutSeconds 120` 延长等待。端口占用时会给出 PID，不会结束占用端口的其他进程。
