# 知行 ZhiXing · 多邮箱工作助理

本地运行的个人邮件 Agent：将多个邮箱中的往来信息整理成可追溯的回答、待办、日程和回复草稿，通过人工审批完成发送，并提供执行 Trace 与费用记录。

适用于 Windows 单机、单用户，支持 QQ、163 和通用 IMAP/SMTP 邮箱。当前是可运行的 MVP；工程回归通过不等于真实邮箱权限、主模型质量或长期稳定性已经验收。

> 邮件、凭证和索引保存在本机。**使用真实模型时，选中的邮件/附件文本、记忆及相关上下文会发送给你配置的模型服务。** 本地 RAG 不代表全链路离线。邮箱凭证使用 Windows 当前用户 DPAPI 加密。

## 核心功能

- **多邮箱与可控收取**：账号独立游标；首次连接只建立基线；历史邮件先预览、再按时间和累计额度导入，支持暂停、继续、取消。
- **本地过滤与线程**：白名单、黑名单、关键词和规则分类；误过滤可恢复，不删除远端邮件；依据 References / In-Reply-To 关联线程。
- **带来源的混合检索**：SQLite FTS5 中文分词 + E5 嵌入 + Qdrant local + RRF 融合 + MiniLM 重排序。模型不可用时明确标记关键词降级。
- **主动邮件感知**：逐账号显式开启后，新邮件进入有每小时额度的分析队列，输出垃圾评分、分类、一句话摘要、理由、待办和日程候选、回复需求及优先级；也可对单封邮件手动分析。模型分数未经校准，仅在高分、较高自评置信度且无白名单或明确行动时自动移入可恢复的本地过滤箱。纠偏生成待确认的偏好记忆，确认后才用于后续分析。
- **待办、日程与摘要**：候选在“待办与跟进”确认或忽略；日程确认时再次检查冲突，并建立提前 30 分钟的本地提醒。真实模式每日汇总前一天邮件；演示模式可手动生成。摘要复用已有感知结果，不重复调用模型。
- **有界邮件 Agent**：最多 6 轮决策、3 次检索、24,000 输入 Token 预算，按需查邮件、附件、历史交互和任务，生成草稿或提出动作。服务端校验账号范围。
- **记忆与人工审批**：长期记忆经确认生效，运行冻结快照；发送绑定账号、草稿版本和审批内容，修改后重新审批；外部结果不明时禁止自动重发。
- **工作台闭环**：待办与跟进、日程、通知未读、审批结果、运行记录；Trace 串联 Agent、审批和工具结果，可查看模型用量、缓存统计与计价，并导出 JSON。
- **文本附件**：支持 PDF、DOCX、TXT 的文本解析，设有大小和处理超时限制；不包含 OCR 或发送附件。

## 快速开始

### 环境要求

- Windows 10/11、现有 Anaconda；脚本默认使用 `D:\Anaconda`，安装在其他位置时需调整 `scripts/install.ps1`。
- 独立 Python 3.11 环境，由安装脚本创建，不向 Anaconda base 安装业务依赖。
- Node.js `20.19+`（20.x）或 `22.12+`，用于前端构建。

建议将仓库放在 E 盘等非 C 盘。脚本将 Python 环境和缓存放在仓库父目录的 `.envs/zhixing`、`.cache`，将数据库、模型、日志和临时文件放在项目内；环境变量仅影响当前进程及子进程。

### 先体验演示模式

在项目根目录 PowerShell 执行：

```powershell
.\scripts\install.ps1
.\scripts\start.ps1
```

首次安装会从 `.env.example` 创建本地 `.env`，默认 `ZHIXING_MODE=demo`；已有 `.env` 不会被覆盖。打开 **http://127.0.0.1:8000**，点击“导入演示邮件”。演示使用固定决策流程，发送明确显示模拟结果，不调用主模型或收发真实邮件。未下载检索模型时会显示关键词降级。

### 启用完整本地检索和真实模型

```powershell
. .\scripts\env.ps1
& $ZhiXingPython scripts\download_mail_models.py
```

下载脚本将模型、版本号与哈希保存在 `data/models`。首次下载需要网络；后续默认复用记录的版本。

在本地 `.env` 配置 `ZHIXING_MODE=live`、`ZHIXING_MODEL_BASE_URL`、`ZHIXING_MODEL_NAME`、`ZHIXING_MODEL_API_KEY`，按需调整超时与每日调用预算，然后重启服务。主模型需兼容 OpenAI Chat Completions 和项目使用的结构化输出契约。

在“邮箱账号”添加授权码、测试连接，再明确启用收取。新账号默认关闭自动分析；历史导入和自动分析分别控制。达到每小时额度的邮件不会自动补分析，可在邮件详情手动分析；手动操作仍受全局模型预算限制。详细说明见 [多邮箱配置](docs/MAIL_SETUP.md)。

### 一次完整操作

收取或导入邮件 → 选择账号范围 → 向“邮件 Agent”提问 → 核对来源与草稿 → 提交审批 → 在“运行记录”或审批条目查看执行 Trace → 在“通知与提醒”确认结果。

审批通过不等于执行成功，SMTP 接受不等于对方已收到。到期提醒当前在 Web 通知中心展示，不是系统桌面推送。

## 技术与结构

后端采用 FastAPI、Pydantic、SQLAlchemy 和 SQLite；Agent 工具循环保存决策状态，LangGraph 负责动作检查点和审批恢复。API 与 Worker 分进程运行。Qdrant 使用本地嵌入式模式，无需 Docker、Redis 或额外部署数据库服务。

```text
backend/
  mail_api.py            邮件核心接口
  mail_observability.py  运行查询、Trace 聚合与工作台接口
  mail_ingest.py         IMAP、游标、线程和过滤
  mail_rag.py            FTS5、Qdrant、嵌入与重排序
  mail_assistant.py      Agent 循环、记忆快照和草稿
  mail_send.py           指定账号的审批发送
  mail_worker.py        邮件持久任务
  runtime.py / tools.py  审批、执行与幂等账本
frontend/src/
  MailApp.tsx            工作台装配与分组导航
  mail/                  页面组件、状态协调与共享类型
  MailTrace.tsx          可导出的执行时间线
  MailActivity.tsx       运行记录与通知
  MailCalendar.tsx       日程管理
scripts/                 安装、启停、备份、迁移和评测
tests/                  后端与故障恢复回归
```

业务数据与全文索引存于 SQLite，向量索引存于 Qdrant；SQLite 同时保留可重建的向量缓存。旧版 UI、飞书适配器和旧单邮箱直发已移除，审批、审计、账本、计价等能力继续复用。

## 测试、备份与验证范围

```powershell
.\scripts\stop.ps1     # 停止项目服务
.\scripts\backup.ps1   # 停服后备份
.\scripts\start.ps1    # 启动 API + Worker
.\scripts\test.ps1     # 完整工程回归并留档
```

浏览器测试需要测试浏览器；首次可在加载 `scripts/env.ps1` 后进入 `frontend` 执行 `npx playwright install chromium`，浏览器缓存将使用项目进程配置的目录。

最近完整验证：**216 项后端测试、120 条合成规则回归、TypeScript/Vite 构建、3 条浏览器流程通过**。测试主要使用合成数据和模拟外部服务；真实邮箱收发、主模型效果与长期运行仍需单独验证。本地检索已有真实模型实验，尚不能据此宣称混合检索普遍优于关键词。

每轮命令、日志、源码快照、JUnit、测试数据库、浏览器截图/录像与 Trace 保存在 `artifacts/test-runs/`，包含失败轮次。本机 `.env`、数据、凭证、模型、日志、备份、测试归档和私人学习资料均由 `.gitignore` 排除；公开仓库保留源码、配置模板、合成测试和必要文档。

## 文档

- [后端模块职责](backend/README.md) · [脚本分类](scripts/README.md) · [测试地图](tests/README.md)

- [工作台使用、代码地图与调试实验](docs/WORKSPACE_GUIDE.md)
- [系统架构](docs/ARCHITECTURE.md) · [多邮箱配置](docs/MAIL_SETUP.md)
- [RAG 与分层记忆](docs/MAIL_RAG_MEMORY.md) · [计价与缓存统计](docs/PRICING.md)
- [测试报告](docs/TESTING_MAIL_AGENT.md) · [测试研究留档](docs/MAIL_TESTING.md)
- [产品评估与局限](docs/PRODUCT_REVIEW.md) · [迁移与恢复](docs/MAIL_MIGRATION.md)

## 许可证

[MIT](LICENSE)。参考项目的许可与来源声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
