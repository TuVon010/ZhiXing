# 后端代码地图

后端采用模块化单体。所有功能仍在一个 API 与一个 Worker 中运行，但代码按职责分包，依赖从入口流向业务与基础设施。

```text
backend/
├─ main.py                    # Uvicorn 兼容入口，仅导出 app
├─ worker.py                  # Worker 兼容入口，仅调用 runner.main
└─ app/
   ├─ main.py                 # FastAPI 应用工厂与路由装配
   ├─ core/                   # 配置、安全边界、共享异常
   ├─ persistence/            # SQLite Store 与事务边界
   ├─ api/                    # 系统级 HTTP 路由、前端静态适配器
   ├─ agent/                  # LangGraph、决策协议、策略、工具和模型调用
   ├─ integrations/           # IMAP、SMTP 等外部协议适配器
   ├─ modules/mail/           # 邮件领域、RAG、感知、草稿和工作项
   │  └─ routes/              # 按账号、导入、消息、Agent 等拆分的路由
   ├─ observability/          # Trace、模型费用和工作台读模型
   └─ workers/                # 调度器与持久邮件任务处理器
```

## 依赖规则

1. `app/main.py` 只组装应用，不实现业务接口。
2. `api` 与 `routes` 负责校验 HTTP 输入和组织输出，不建立 IMAP/SMTP 连接。
3. `modules` 实现业务用例，可以依赖 `persistence`、`agent` 和受控外部集成。
4. `agent/tools.py` 是模型决策进入业务写操作的唯一受控入口。
5. `workers` 领取持久任务后调用业务模块；业务模块不能反向启动 Worker。
6. `observability` 读取运行、审计和模型调用，不改变 Agent 决策。
7. 根目录 `backend/main.py` 与 `backend/worker.py` 是部署兼容层，不放新功能。

建议阅读：`app/main.py → api/router.py → modules/mail/routes/assistant.py → modules/mail/assistant.py → agent/graph.py → agent/tools.py → modules/mail/sending.py → observability/mail.py`。

更完整的职责、调用链和新增功能示例见 [代码结构与开发规范](../docs/CODE_STRUCTURE.md)。
