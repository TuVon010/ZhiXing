# 设置与计价

## 功能与使用

当前页可按邮箱读写过滤规则 JSON、重建该邮箱本地检索索引、查看旧数据迁移复核，以及配置模型计价。计价表允许分别设置未命中输入、缓存命中输入、输出的单价，必要时开启空闲/高峰分段计价和时区、时段、节假日规则。字段留空沿用默认值，填 `0` 表示明确零价；保存只影响后续调用。密钥不在此页回显，主模型通过本地被 Git 忽略的 `.env` 配置。

## 实现链路

[`SettingsPage.tsx`](../../frontend/src/mail/SettingsPage.tsx) 调 `/mail/accounts/{id}/filters`、`/reindex`、`/mail/migration`；[`PricingSettings.tsx`](../../frontend/src/PricingSettings.tsx) 调 `GET /pricing`、`POST /pricing/{profile_id}`。过滤规则请求由 [`mail_api.py`](../../backend/mail_api.py) 的 `FilterRules` 验证、去重并增版本；收取时由 [`filtering.py`](../../backend/filtering.py) 应用，旧邮件不会仅因改规则自动重分类。重建索引进入持久 Worker 队列。

[`billing.py`](../../backend/billing.py) 管理模型价格、覆盖值、分段时段和费用估算；[`planner.py`](../../backend/planner.py) 在模型调用时保存价格版本和用量快照。Trace 的费用摘要只统计可核对数据；提供方未返回缓存 Token 或缺有效价格时显示“未知”，不把未知当 0，也不按新价格追溯改写旧记录。详见 [计价与缓存统计](../PRICING.md)。

## 调试提示

检查三层：模型 API 是否返回 `usage`；其中是否有缓存命中字段；调用时价格快照是否适用该模型/时段。只靠输入总 Token 无法反推出缓存命中率。配置冲突或改币种时优先看计价表字段来源和版本；测试见 [`tests/test_billing.py`](../../tests/test_billing.py)。
