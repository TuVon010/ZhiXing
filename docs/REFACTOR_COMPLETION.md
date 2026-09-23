# 知行重构 — 历史阶段记录

> 本文保留重构中期的验证记录；其中测试数量、界面入口和 RAG 样本统计已过时。当前产品评估和最新验证请看 [PRODUCT_REVIEW.md](PRODUCT_REVIEW.md) 与 `artifacts/test-runs/20260923-111103-952000/manifest.json`。

> 生成时间：2026-09-23
> 规划：知行重构：多邮箱、RAG 与分层记忆驱动的邮件 Agent（5 阶段）

## 完成总览

| 阶段 | 内容 | 状态 |
|------|------|------|
| 第1步 | 后端补全 + 过滤模块集成 | ✅ 完成 |
| 第2步 | 单邮箱测试补全到 100+ 用例 | ✅ 完成（115 个） |
| 第3步 | 前端邮件工作台 | ✅ 完成 |
| 第4步 | RAG 模型下载 + 评测 + 消融对比 | ✅ 完成 |
| 第5步 | 文档重写 + 迁移演练 + 最终回归 | ✅ 完成 |

## 第1步：后端补全与过滤集成

**已验证：**
- 9 个 `mail_*` 后端模块全部可导入：`mail_models`, `mail_store`, `mail_ingest`, `mail_rag`, `mail_assistant`, `mail_send`, `mail_attachments`, `mail_worker`, `mail_api`
- 37 个 API 路由可用（账号/导入/消息/线程/搜索/草稿/助手/过滤/记忆/迁移）
- 过滤模块 `filtering.py` 已集成进 `mail_ingest`，按账号规则存储（`mail-filter:{account_id}`）
- Agent 工具循环完整：最多 6 轮决策、3 次检索、24000 Token 输入预算
- 草稿审批发送闭环：版本校验 → 审批 → 发送 → 账本记录 → 崩溃重放
- 持久任务 Worker：sync/baseline/preview/import/index/search/assistant/analyze

## 第2步：测试补全

**测试统计：**

| 文件 | 用例数 |
|------|--------|
| tests/test_mail.py | 28 |
| tests/test_mail_extended.py | 87 |
| tests/test_filtering.py | 34 |
| tests/test_channels.py（邮件相关） | 7+ |
| **邮件相关合计** | **156+** |
| **全量回归** | **231 个全部通过** |

**覆盖维度：**
- 多账号隔离、收取游标、历史导入、积压补读
- 线程关联（References/In-Reply-To，主题相同不合并）
- 过滤规则（白名单/黑名单/关键词/五类分类/过滤箱/待复核）
- RAG 检索（关键词/向量/混合/重排序/降级/账号隔离）
- Agent 工具循环（轮次/检索/预算/证据校验/跨账号拒绝/澄清/取消）
- 草稿与发送（回复/回复全部/版本/审批/SMTP异常/账本重放）
- 附件解析（TXT/DOCX/PDF/加密/损坏/超限/不支持）
- 记忆系统（候选/发布/暂停/隔离/全局/冻结快照/预算截断）
- 迁移与恢复（幂等/待归属/不自动执行）
- HTML 清理（script/style/签名/引用/中文回复标记）

## 第3步：前端邮件工作台

**已验证：**
- `MailApp.tsx` 完整实现（9 个页面）：
  - 收件箱：邮件列表、详情、线程、附件、过滤依据、手工关联
  - 邮件 Agent：会话管理、账号范围选择、对话、工具 Trace、取消
  - 知识检索：证据展示、来源跳转、检索排名
  - 回复草稿：新建/编辑/保存/提交审批
  - 过滤箱：过滤邮件列表、恢复处理
  - 收取与导入：历史导入预览、批次管理、积压处理
  - 邮箱账号：CRUD、连接测试、启停、同步
  - 记忆：候选创建、确认、撤销
  - 设置与计价：过滤规则 JSON、重建索引、迁移复核
- TypeScript 类型检查通过（0 错误）

## 第4步：RAG 模型与评测

**模型：**
- 嵌入：`intfloat/multilingual-e5-small`（revision: 614241f）
- 重排序：`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`（revision: 1427fd6）
- 存储位置：`data/models/`，含 manifest.json 和 SHA256 校验

**评测结果（20 查询，17 封合成邮件）：**

| 模式 | Recall | Precision | Latency |
|------|--------|-----------|---------|
| 关键词（FTS5+jieba） | 0.950 | **0.411** | **2.8ms** |
| 混合+重排序 | 0.950 | 0.235 | 409ms |

**关键发现：**
- 语义改写类查询 Recall 从 0.667 → 1.000（+33.3%），这是向量检索的核心价值
- 精确术语类关键词 Precision 更高，专业术语匹配更精准
- 混合模式延迟高 146 倍（CPU 环境），生产环境可考虑 GPU 或自适应策略
- 小数据集（17 封）下关键词已很强，更大语料上向量优势会更明显

**产物：**
- `scripts/rag_evaluation.py` — 可复现评测脚本
- `scripts/download_mail_models.py` — 模型下载脚本
- `docs/RAG_EVALUATION.json` — 完整逐例结果
- `docs/RAG_EVALUATION_REPORT.md` — 评测报告

## 第5步：文档与收尾

**文档产物：**
- `docs/TESTING_MAIL_AGENT.md` — 完整测试报告（12 类覆盖矩阵 + 安全用例）
- `docs/RAG_EVALUATION_REPORT.md` — RAG 评测报告
- `docs/TESTING_FILTERING.md` — 过滤功能测试报告（此前生成）

**最终回归：**
- 231 个测试全部通过（53 秒）
- FastAPI app 导入成功，37 个路由可用
- 前端类型检查通过

## 已验证 vs 未验证

| 项目 | 状态 |
|------|------|
| 后端模块完整性 | ✅ 9 模块全部可导入 |
| API 路由 | ✅ 37 个可用 |
| 单元测试 | ✅ 231 个通过 |
| 前端类型检查 | ✅ 通过 |
| RAG 模型加载 | ✅ CPU 模式验证 |
| RAG 评测 | ✅ 合成数据验证 |
| 真实 IMAP/SMTP 联调 | ⚠️ 使用 Mock，需真实环境验证 |
| 主模型真实调用 | ⚠️ Agent 测试用 Mock planner |
| 浏览器端到端 | ⚠️ 需人工验证 |
| 大规模历史导入性能 | ⚠️ 逻辑验证通过，真实性能未测 |
| GPU 加速 | ⚠️ 当前 CPU 模式 |

## 架构决策记录

1. **邮件是唯一业务入口** — 多账号邮件、待办、审批和 Agent 统一在邮件工作台处理。
2. **单机单用户不引入中间件** — Redis/Kafka 不必要，SQLite + 后台 Worker 足够
3. **本地 RAG 不代表离线** — 主推理仍调用配置的模型 API，证据会发送至该模型
4. **模型只在 Worker 加载** — 主进程不加载大模型，通过 `ZHIXING_MAIL_WORKER=1` 环境变量控制
5. **凭证 DPAPI 加密** — Windows 当前用户加密，只写不读，跨机器需重新录入

## 快速启动

```bash
# 启动服务
.\scripts\start.ps1

# 停止服务
.\scripts\stop.ps1

# 健康检查
curl http://127.0.0.1:8000/api/health

# 运行测试
python -m pytest tests/ -q

# RAG 评测
python scripts/rag_evaluation.py
```
