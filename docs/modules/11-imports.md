# 收取与导入

## 功能与使用

针对所选邮箱设置历史时间范围和本批累计上限，先“预览范围”，再决定是否创建导入批次。预览不下载正文、不推进实时游标；手工导入可暂停、继续、取消。账号开启“自动补齐最近邮件”后，系统每天建立一个滚动批次，默认覆盖最近 7 天，也可在账号页设置 1～30 天；启用、修改设置或手动同步时会立即尝试当日批次。范围重叠时按账号、文件夹和邮件来源指纹跳过已有邮件，页面分别显示“本次新增”和“已存在跳过”。

历史导入默认只入库和索引；可勾选本批放行邮件最多 20 封进入 Agent 分析，或导入后使用“分析待处理邮件”显式补分析。自动补齐与自动模型分析是两个开关，只有同时开启时自动批次才会排入分析。页面显示已入库、已产出、待分析、排队、失败和过滤数量。测试邮箱只能分析本地合成邮件，不能远端收取。

## 实现链路

[`ImportsPage.tsx`](../../frontend/src/features/imports/ImportsPage.tsx) 对应 `/mail/imports/preview`、`/mail/imports`、`/mail/imports/{id}/{operation}`、`/mail/accounts/{id}/perception/summary` 和 `/perception/batch`。持久 `mail_jobs` 由 [`mail_jobs.py`](../../backend/app/workers/mail_jobs.py) 租约领取；IMAP 扫描、服务端收件时间过滤、UID 游标和导入批次状态在 [`ingestion.py`](../../backend/app/modules/mail/ingestion.py)。批量分析队列与去重在 [`perception_queue.py`](../../backend/app/modules/mail/perception_queue.py)。

实时收取、滚动补齐与手工历史导入使用不同控制：首次连接从现在建立实时游标，不回扫全部历史；滚动补齐按账号每天一次，手工导入使用用户指定范围；断线补读有时间和总量上限，剩余由用户选择。默认每轮扫描数量、轮询和每小时自动分析额度在邮箱账号设置；全局模型预算另行限制。模型分析会把邮件文本发送至配置的模型服务，可能产生费用。

## 调试提示

区分四个状态：IMAP 收取成功、邮件入库、索引完成、感知完成。若没有摘要先查批次是否勾选分析及 `perception/summary`；失败任务看 `/mail/jobs/{id}`，允许的读取/分析失败可显式重试。测试覆盖见 [`tests/test_mail.py`](../../tests/test_mail.py) 与 [`tests/test_mail_perception_queue.py`](../../tests/test_mail_perception_queue.py)。
