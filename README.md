# 知行 ZhiXing · 多邮箱工作助理

在本机管理多个 QQ、163 和通用 IMAP/SMTP 邮箱：可控收取、过滤复核、往来线程、带来源的知识检索、邮件 Agent、待办与跟进、回复草稿和人工审批发送。

Python 3.11 / FastAPI / LangGraph / SQLite + Qdrant local / React。API 与 Worker 分开运行，无 Docker 或 Redis。SQLite 保存业务数据与全文索引，Qdrant local 保存并检索本地向量。主推理使用配置的模型 API；本地嵌入和重排序只负责检索，**被选中的邮件证据仍会交给主模型服务**。

## 启动

在项目根目录 PowerShell 执行：

```powershell
.\scripts\install.ps1
.\scripts\env.ps1
& $ZhiXingPython scripts\download_mail_models.py
# 升级既有安装：先停服、备份，再迁移
.\scripts\stop.ps1
& $ZhiXingPython scripts\migrate_mail.py
.\scripts\start.ps1
```

打开 http://127.0.0.1:8000 。在“邮箱账号”添加账号、测试连接，再启用收取。默认每轮扫描 20 封；首次连接只建立当前基线，不下载全部历史邮件。历史导入先预览，再按时间范围和累计额度执行。新账号默认关闭自动分析，收取和索引不会直接调用主模型。

.env 中 ZHIXING_MODE=demo 启用离线业务演示，新工作台提供“导入演示邮件”。演示 Agent 使用固定检索流程，发送显示模拟结果，不能据此推断真实模型效果。live 模式不会将失败偷偷替换为演示成功。

## 主要行为

- 每个账号独立游标、凭证、线程和知识作用域。统一收件箱可以浏览全部；检索和 Agent 必须明确选择账号。
- IMAP/SMTP 强制验证 TLS。Windows DPAPI 加密凭证，只写不读；数据库只保存引用。
- 过滤和归档只修改本地状态。待复核与被过滤邮件不进入模型上下文和检索。
- 文本型 PDF、DOCX、TXT 由受限子进程解析；不支持 OCR 或发信附件。
- FTS5 中文分词、E5-small 向量、RRF 融合、MiniLM 重排序。模型不可用时明确标记关键词降级。
- Agent 最多 6 轮决策、3 次检索、24,000 输入 Token，可按证据继续查询、生成草稿或提出动作。
- 长期记忆须人工确认、按账号隔离；运行冻结快照，生成可读 USER.md / MEMORY.md。
- 发信始终审批。草稿修改使旧审批失效，SMTP 接受不代表送达，结果不明禁止自动重发。
- 待办、审批、记忆和 Trace 都在邮件工作台内完成，不再保留旧版工作台入口。

## 环境与数据

Python 环境：E:\postgraduateLife\intern\.envs\zhixing；Anaconda：D:\Anaconda。模型位于 data/models；依赖、Hugging Face、PyTorch 和浏览器缓存由 scripts/env.ps1 指向 E 盘。不向 base 安装业务依赖。

业务库与检查点为 data/zhixing.db 和 data/checkpoints.db。凭证、邮件、附件、模型、快照、测试资料、备份及 .env 均被 Git 忽略。测试默认使用合成数据；导出的 Trace 可能包含工作内容。

## 文档与验证

- [架构与边界](docs/ARCHITECTURE.md)
- [多邮箱配置](docs/MAIL_SETUP.md)
- [RAG、记忆与 Agent](docs/MAIL_RAG_MEMORY.md)
- [迁移与恢复](docs/MAIL_MIGRATION.md)
- [测试与研究复现](docs/MAIL_TESTING.md)
- [计价与缓存统计](docs/PRICING.md)

```powershell
.\scripts\test.ps1
..\scripts\env.ps1
& $ZhiXingPython scripts\evaluate_mail.py
```

第一条运行完整工程回归并留档；第二条评测脚本使用真实本地模型进行 200 场景检索对比。模拟 SMTP/IMAP 通过不等于真实邮箱联调。主模型质量和真实账号权限分别验证，不预设混合检索一定更好。

MIT 许可证和参考来源声明保留。pulse-agent 为参考工程，未引入 Harness、Cloudflare 和项目管理运行链。
