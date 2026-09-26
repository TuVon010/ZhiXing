# 代码结构与开发规范

## 架构选择

知行采用“模块化单体 + 前后端分离 + 分层依赖”。邮件、Agent、检索和工作项在一个代码仓库与进程组中交付，但模块通过明确入口协作，避免当前个人应用过早引入微服务、消息中间件和分布式事务。

前端源码和后端源码互不导入。开发时 Vite 通过 HTTP 调用 FastAPI；发布时后端可以挂载已经构建的 `frontend/dist`，该适配仅用于一键本地启动。

## 一次请求如何流动

以“询问邮件并生成回复草稿”为例：

```text
AssistantPage
  → shared/api/client.ts
  → modules/mail/routes/assistant.py
  → modules/mail/assistant.py
  → retrieval.py / repository.py
  → agent/model_client.py
  → 本地 mail_draft
  → 用户在草稿页最终确认
  → agent/graph.py
  → agent/tools.py
  → modules/mail/sending.py
  → execution ledger + Trace
```

路由只处理协议；`assistant.py` 保存有界决策状态；`model_client.py` 统一记录模型用量；`tools.py` 校验副作用；`sending.py` 只发送经过版本和哈希复核的草稿。

## 后端各层职责

### `core`

保存整个应用共享且不属于具体业务的能力。`config.py` 读取环境变量；`security.py` 实现本地 Host、Origin、Cookie 和写请求标记检查；`exceptions.py` 定义外部结果不明等跨模块异常。

### `persistence`

`Store` 封装 SQLite 连接、事务、通用记录和审计。需要多条写入保持原子性时，应接收同一个连接并在一个事务中完成。不要在前端路由中编写数据库事务。

### `api` 和 `modules/mail/routes`

负责 Pydantic 输入校验、账号范围入口和 HTTP 返回。路由函数不应包含模型 Prompt、IMAP 协议细节或复杂 SQL。系统设置、计价等跨领域接口位于 `api/routes`；邮件接口位于 `modules/mail/routes`。

### `modules/mail`

- `repository.py`：邮件表、账号范围、凭证引用和持久队列。
- `ingestion.py`：IMAP、UID 游标、正文解析和线程归并。
- `filtering.py` / `perception.py`：规则过滤与模型感知。
- `retrieval.py`：FTS5、Qdrant、RRF 和重排序。
- `assistant.py`：会话、记忆快照、有界工具循环和草稿。
- `work_items.py` / `calendar.py` / `digest.py`：待办、日程、提醒和摘要。
- `sending.py`：SMTP 外部写入及不明结果处理。

### `agent`

`schemas.py` 定义动作和状态；`model_client.py` 是模型调用边界；`policy.py` 判断风险；`graph.py` 负责 LangGraph checkpoint、中断恢复和动作推进；`tools.py` 负责参数校验、执行账本和确定性工具调用。模型不能绕过这一层直接发送邮件。

### `workers`

`runner.py` 负责调度和心跳，`mail_jobs.py` 负责租约领取及任务分派。API 只负责入队，耗时收取、索引、感知和 Agent 运行由 Worker 执行。

### `integrations`

封装 IMAP、SMTP 等外部协议连接与传输细节。业务模块负责决定“收取什么、发送什么”，适配器只负责“怎样连接并传输”，因此更换邮箱服务商不会把协议代码扩散到路由和 Agent。

### `observability`

汇总运行、审计、模型调用、Token、缓存命中和费用。这里记录显式决策和工具结果，不保存或展示模型隐藏思维链。

## 前端职责

`features` 按用户看到的业务能力划分。页面通过 `app/workspace/context.ts` 获取当前账号和公共交互状态，通过 `shared/api/client.ts` 请求后端。OpenAPI 类型生成到 `shared/api/generated.ts`，不手写一套与接口重复的 DTO。

页面专属组件留在对应 feature；跨两个以上 feature 的组件才进入 `shared`；服务端数据不能复制为多个互不一致的全局状态。

## 注释标准

注释用于解释边界和原因：

- 模块 Docstring 说明职责和禁止事项；
- 公共函数说明事务、幂等、账号范围和异常；
- 对 SMTP 不明结果、执行账本、租约等约束解释“为什么”；
- 不给一眼可见的赋值和循环逐行翻译中文。

## 新增功能示例

新增“邮件稍后处理”功能时：

1. 在 `modules/mail/schemas.py` 增加输入结构。
2. 在 `modules/mail/work_items.py` 编写业务用例和状态约束。
3. 在 `modules/mail/routes/work_items.py` 暴露接口。
4. 在 `features/work-items` 添加界面。
5. 用 OpenAPI 重新生成前端类型。
6. 增加业务规则验证和端到端场景，保留 Trace 与测试归档。

不要把实现直接写入 `app/main.py`、根目录兼容入口或前端 `main.tsx`。
