# 后端模块地图

邮件模块按业务命名，共享模块按能力命名。没有 mail_ 前缀不意味着旧代码或失效功能。

| 层次 | 模块 | 当前用途 |
| --- | --- | --- |
| 应用入口 | main、config、db | HTTP 会话与 API、配置、SQLite 事务与记录 |
| 邮件业务 | mail_models/store/ingest/api | 账号、凭证、收取游标、线程、过滤和接口 |
| 邮件 Agent | mail_assistant、mail_rag、mail_attachments | 有界工具决策、证据检索、记忆快照、附件文本 |
| 后台任务 | worker、mail_worker | 邮件/动作队列轮转、租约、提醒和日程 |
| 安全执行核心 | runtime、policy、tools、schemas | LangGraph、审批、参数校验、执行账本 |
| 模型与费用核心 | planner、billing | 模型请求、预算、用量、缓存命中和价格快照 |
| 可观测核心 | tracing、mail_observability | 原运行审计、父子 Trace 聚合、工作台查询 |
| 发信 | mail_send、channels | 当前账号草稿 SMTP、外部结果不明的共享异常 |
| 可选演进能力 | evolution | Skill/Parser 样本、评测、人工发布和回滚；定时生成默认关闭 |

`planner` 除规划外还提供统一 `model_json` 调用，邮件 Agent、演进等共享预算/计价记录。`evolution` 提供内置 Skill 初始化，不能直接当作死代码删除。`channels.py` 只定义异常，没有旧飞书或单邮箱直发适配器。

建议阅读：mail_api → mail_store/mail_worker → mail_assistant → runtime → tools/mail_send → tracing。业务流程和接口调试见 [工作台指南](../docs/WORKSPACE_GUIDE.md)。
