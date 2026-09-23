# 模型缓存命中率与分段计费设计

状态：**部分实现**。2026-09-22 已接入自定义参数、逐项默认回退、分时估算、缓存字段规范化、每次调用价格快照和分币种汇总，使用方式与实际边界见 [计价说明](../PRICING.md)。独立账单核对、完整阶梯规则、日维度图表等仍为后续设计。下文保留设计时基线 `ca3e3e4` 和完整目标，不代表每项均已交付。

配套文件：[机器可读算例](cache-billing-examples.json)、[本轮设计核对记录](CACHE_AND_BILLING_REVIEW.md)。

## 1. 要解决的问题

让知行能回答：这次调用输入了多少 Token，其中多少命中供应商缓存；为什么按这个价格估算；一天花了多少，缓存优惠节省多少；哪些数据未知；模型涨价后原来的账是否还解释得清楚。

首版覆盖 DeepSeek 官方直连接口的输入命中/未命中、输出、人民币高峰/空闲价格，以及 Jev 已有的独立调用成本。第三方中转商价格必须独立配置，不能因为模型名叫 deepseek 就套用官方费用。

## 2. 当前已经有什么，还缺什么

| 代码位置 | 已有能力 | 本设计补充 |
| --- | --- | --- |
| `backend/planner.py::model_json` | 保存完整 usage，按输入价和输出价计算 cost | 规范化缓存字段、供应商信息、价格快照和费用明细 |
| `backend/tracing.py::detail` | 按 run 汇总主模型用量，单独汇总 Jev | 缓存命中率、已知/未知费用、分币种汇总 |
| `backend/main.py::stats` | 全局用量和费用、Parser 命中率 | 时间过滤、缓存统计覆盖率、取消最近 10000 条截断式财务统计 |
| `backend/jev.py` | 独立用量与输入费用 | 适配同一计费明细格式；无缓存证据时保持不适用或未知 |
| `eval/run.py` | 离线/真实规划评测及平均费用 | 输出缓存用量、计费覆盖率、单任务完整模型开销 |
| 前端 Trace / 设置 | 模型调用、usage、简单价格配置 | 价格规则、缓存与费用解释、规则版本和估算标签 |

因此，如果过去的真实响应 usage 已含缓存字段，可以补算缓存指标；没有返回的字段不能事后从提示词长度猜出来。历史费用还需要当时供应商、价格和时间依据，不能一律用今天的价格覆盖。

## 3. 已核对的供应商事实

DeepSeek usage 提供 `prompt_cache_hit_tokens` 和 `prompt_cache_miss_tokens`；Chat Completions 文档同时列出 `prompt_tokens_details.cached_tokens`，它与前者表示同一命中用量，不能相加。输入总量等于命中与未命中之和。[接口说明](https://api-docs.deepseek.com/api/create-chat-completion/)

截至核对日，官方人民币价格如下，单位都是元 / 百万 Token：

| 模型 | 时段 | 输入命中 | 输入未命中 | 输出 |
| --- | --- | ---: | ---: | ---: |
| deepseek-flash | 空闲 | 0.02 | 1 | 4 |
| deepseek-flash | 高峰 | 0.04 | 2 | 8 |
| deepseek-v4-pro | 空闲 | 0.15 | 4.5 | 13.5 |
| deepseek-v4-pro | 高峰 | 0.30 | 9 | 27 |

高峰为北京时间周一至周五、排除中国法定节假日的 09:00–12:00、14:00–18:00，其余为空闲。旧 Flash 名称需要按版本化映射处理。[官方价格页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)

这是查询当日的公开价，不是本项目已验证的账单，也不是永久价格。查阅的页面未明确请求跨时段时按哪个计费时间点归属，`created` 是响应创建时间而非已被证明的结算时刻，设计必须保留这一区别。

缓存由供应商管理，重复前缀有机会被复用，但构建与命中是尽力而为，不能保证连续两次请求一定命中；它复用前缀计算，并不是直接返回上次答案。[缓存说明](https://api-docs.deepseek.com/zh-cn/guides/kv_cache/)

## 4. 三种命中率必须分开

### 4.1 输入 Token 缓存命中率——首页主指标

```text
单次命中率 = cache_read_tokens / input_tokens
区间命中率 = Σ cache_read_tokens / Σ input_tokens
```

分子分母来自同一批缓存字段完整、校验通过的调用；只对供应商缓存可观测的调用统计。分母为零时显示“无可统计输入”，不显示 0% 或 100%。

不能平均各请求百分比。例如一次 100 个输入、全命中，另一次 9900 个输入、全未命中，总体为 1%，不是 50%。

### 4.2 请求缓存命中率——辅助指标

```text
至少命中 1 个 Token 的请求数 / 缓存状态已知且输入大于 0 的请求数
```

它说明多少请求受益，不说明节省了多少计算。两个比例在页面明确命名。

### 4.3 Parser 命中率——继续单列

Parser 是本地确定性解析成功而跳过主规划模型，与供应商缓存不同；它不产生“输入 Token 全命中”的模型请求。Jev 影子评审仍可能发生，要独立计入总成本。

### 4.4 缺失数据的覆盖率

同时显示 `eligible_calls`（供应商支持缓存统计的尝试数）、`cache_observed_calls`、`missing_usage_calls`、`invalid_usage_calls`、`not_applicable_calls`。命中率旁显示“已知样本 18 / 20 次”。

另给 `observed_input_token_coverage`：缓存拆分已知的输入量 / 所有输入总量已知的适用调用输入量。若存在连输入量都未知的调用，明确标注“此覆盖率仅限输入已知部分”，不要误称全量 Token 覆盖率。

不支持公开缓存统计的 Jev 不混入 DeepSeek 分母。演示和 mock 数据单独分组，不混入真实使用看板。

## 5. 用量规范化规则

新增小模块 `backend/usage.py`，只处理响应字段和来源，不承担网络或权限。保留 `usage_raw`，输出带 `normalizer_version` 的规范化快照。

| 规范字段 | DeepSeek 来源或含义 |
| --- | --- |
| input_tokens | prompt_tokens |
| cache_read_tokens | prompt_cache_hit_tokens；兼容 nested cached_tokens |
| uncached_input_tokens | prompt_cache_miss_tokens |
| output_tokens | completion_tokens |
| reasoning_tokens | completion_tokens_details.reasoning_tokens，输出子集 |
| total_tokens | total_tokens，校验与适配器定义一致 |
| cache_write_tokens | 当前 DeepSeek 无独立缓存写入计价项，标为不适用 |
| cache_observability | observed / derived / unavailable / not_applicable / invalid |
| field_provenance | 字段来自哪个 JSON 路径，哪些值由守恒关系推导 |

具体规则：

1. 计数只接受非负整数；拒绝 bool、负数、浮点、字符串、NaN。缺失与 0 分开。
2. 同时存在两个命中字段时必须一致，否则标 invalid；不静默选择较便宜的值。
3. total input 与 hit 已知且 0 ≤ hit ≤ input，可推导 miss；只有 hit/miss 已知可推导 input；记录 derived，不伪装原始返回。
4. 只有 input，没有任何缓存拆分字段：命中率未知，不默认 hit=0。
5. 验证 hit + miss = input，reasoning ≤ output；不一致时保留原始数据，停止精确缓存计价。
6. reasoning 已计入 output，不再额外加一遍；不按可见回答字数估算完整输出。
7. 模型返回格式错误但 usage 有效，仍需计入消耗；业务失败不等于费用为零。
8. 网络超时没有 usage，标 usage_unknown；本地调用次数已消耗，费用待核对，不当免费。

其他供应商必须有自己的映射及包含关系。特别是缓存写入量，有的协议在 input 外计数，有的作为子集；未定义映射时拒绝套用 DeepSeek 守恒公式。

## 6. 价格规则：独立于模型调用代码

新增 `backend/billing.py` 负责选择价格、生成 Decimal 明细。模型名、币种、时段和历史版本属于配置数据，不散落在调用函数中。

### 6.1 价格表的身份

```text
provider_id + account_profile + endpoint_family + model_selector
            + currency + effective_interval + price_version
```

provider_id 区分 deepseek_official、typesafe_official、custom_gateway。account_profile 是本地无密钥标识，允许账号合同价不同；不要存 API key 或把 key 放进价格匹配规则。网关只支持兼容协议，并不说明它采用官方价格。

每张价格表保留：source_url、source_checked_at、verified_effective_from/to、observed_at、currency、计费单位、模型别名映射、calendar_version、规则正文与摘要哈希、draft/active/retired 状态。

未知历史生效时间保留 null：不能把网页抓取日期伪称涨价生效日。用户确认后可以指定 `local_apply_from` 用于今后估算，历史区间仍未验证。后续改价新建版本，不编辑旧快照。

### 6.2 多种“分段”分别建模

| 分段种类 | 规则语义 | 第一版处理 |
| --- | --- | --- |
| 高峰 / 空闲 | 按时间和日历选不同单价 | 实现 |
| 缓存命中 / 未命中 | 同一请求的不同输入部分分别计价 | 实现 |
| 模型历史涨价 | 按生效时间选择价表版本 | 实现 |
| 上下文长度阶梯 | 可能整单换价或按区间累进 | 预留类型，未支持时明确拒绝 |
| 月用量阶梯 / 套餐 / 赠金 | 依赖账号累计和账单结算 | 不假装由单次 usage 算出 |
| 缓存写入、缓存 TTL、Batch 折扣 | 供应商专有口径 | 独立适配后再开启 |

不能看到“超过 128K”就按税率式累进，也不能默认整个请求高价；以提供商合同为准。当前截图体现的是分时与缓存分类，没有提供上下文阶梯规则。

### 6.3 时间与日历

保存 UTC 请求发出时间、响应接收时间、供应商 created，分类时转换到 Asia/Shanghai。统计日的时区与计费时区分别记录，不能用机器当前时区隐式决定。

本地规则采用半开区间 `[09:00,12:00)`、`[14:00,18:00)` 作为明确估算约定，测试边界秒。周末按当前公开规则为空闲，不用通用“中国调休工作日”函数把补班周六强制当高峰。

维护版本化的供应商节假日日历：包含适用年份、已确认节假日、校验来源、完整性标记和人工覆盖记录。法定日与连休范围有疑义时待确认。周末或日内非高峰时段不依赖节假日即可判断；处在周一至周五高峰候选区间、日历未完整时不能默认已知。

供应商未说明归属时间点时，默认用请求发出时刻给“估算时段”；明确显示时间依据。请求跨越时段、价格版本边界或假日日历未知时，额外给候选费用区间与 `period_uncertain`。不按请求运行了几秒自动拆 Token：不知道每个 Token 的供应商结算时间。

若未来能拿到账单计费时段，则它优先用于对账版本，保留本地初始估算。

## 7. 计算方式与可解释明细

### 7.1 DeepSeek 单次费用

```text
费用 = (缓存命中输入 × 命中价
      + 缓存未命中输入 × 未命中价
      + 输出 × 输出价) / 1,000,000
```

用 Decimal 从字符串单价构造，内部保留足够精度；API 金额用字符串传输，显示时再格式化。不要每次先四舍五入到分再汇总。零费用也需证据：模拟数据标 simulated，真实 0 要有明确零价或零用量。

### 7.2 示意算例，不是你的真实账单

设 Flash 输入 10000 Token，其中命中 8000、未命中 2000，输出 1000。采用上表高峰价：

```text
命中率 = 8000 / 10000 = 80%
命中部分 = 8000 × 0.04 / 1,000,000 = ¥0.00032
未命中部分 = 2000 × 2 / 1,000,000 = ¥0.004
输出部分 = 1000 × 8 / 1,000,000 = ¥0.008
合计 = ¥0.01232
```

同用量空闲时段估算 ¥0.00616。没有缓存优惠、同高峰时段的反事实基线是 ¥0.028，因此缓存优惠估算节省 ¥0.01568，占这个基线的 56%。80% Token 命中率不等于总费用减免 80%。

### 7.3 优惠归因避免重复

默认只显示“同模型、同时段、相同输入输出量，但输入全部按未命中价”的缓存节省；这只是可解释反事实，不是钱包返现。

若另显示空闲优惠，采用固定归因顺序：高峰全未命中 → 实际时段全未命中 → 实际时段实际缓存。两步差额可相加，不再把另一个重叠基线的优惠也加进来。跨币种不直接相加；若做汇率视图，另记汇率来源和时点，不改原币金额。

### 7.4 费用状态

`estimated`：usage 和规则足以算出本地估算；`range_estimate`：存在时段或缓存拆分不确定；`unknown`：缺重要证据；`provider_reported`：供应商明确返回费用；`reconciled`：有匹配账单依据；`simulated`：离线测试。

主模型调用响应中的 usage 是用量证据，不是扣款凭证。“请求成功”“已保存 cost”都不能直接标成 reconciled。余额前后差可能含其他调用和赠金扣减，不适合作为单次精确对账。

缓存字段缺失但 input/output 和价格已知时，可给“全命中至全未命中”的估算区间，同时命中率保持未知。不能悄悄按全未命中生成精确费用。

## 8. 数据结构和持久化

先沿用 records，不新建财务系统。模型调用继续保留 `model_call` / `jev_call`；增加 `price_profile`、`billing_record`，必要时 `billing_reconciliation`。

一次网络尝试对应稳定 `call_id + attempt_id`。断线重试是新尝试，不能因为 payload 相同就把可能计费的重复尝试抹掉；重复上报同一次响应通过唯一键去重。审批恢复未发出新调用时不得生成新账单。

`billing_record` 建议保存：

```json
{
  "call_id": "example-call",
  "attempt_id": "1",
  "run_id": "example-run",
  "provider_id": "deepseek_official",
  "account_profile": "personal-default",
  "requested_model": "deepseek-flash",
  "response_model": "deepseek-flash",
  "purpose": "planning",
  "usage_snapshot_id": "example-usage",
  "price_snapshot_id": "example-price-v1",
  "calendar_snapshot_id": "example-calendar-v1",
  "billing_engine_version": "1",
  "period": "peak",
  "time_basis": "client_request_started_estimate",
  "status": "estimated",
  "currency": "CNY",
  "amount": "0.01232",
  "cache_savings": "0.01568",
  "items": [
    {"kind": "input_cache_read", "tokens": 8000, "per_million": "0.04", "amount": "0.00032"},
    {"kind": "input_uncached", "tokens": 2000, "per_million": "2", "amount": "0.004"},
    {"kind": "output", "tokens": 1000, "per_million": "8", "amount": "0.008"}
  ]
}
```

用量、价格和日历快照要真的持久化并可查询，不能只存 ID 却覆盖原内容。上面的 ID 和模型响应均为设计示例，不表示已调用。

价格修正和历史补算追加新 revision，保留旧版费用及 supersedes_id。汇总通过显式选定的当前核算版本去重，不把所有 revisions 相加。支持“当时估算”和“按后来证据校正”的两种视图。

Jev 使用相同明细结构但自己的 provider/usage 适配，缓存不适用，输出即使记录也不能任意套 DeepSeek 输出价；当前 Jev 价格未知则它的费用继续未知。

## 9. 汇总不能悄悄丢记录

按明确时间范围从数据库聚合全部适用记录，不沿用最近 10000 条截断。分页只影响明细列表，不影响总计。按 provider、model、purpose、currency、模拟/真实分组；模型名别名解析保留原始值。

返回 `known_amount_by_currency`、unknown/range/reconciled 调用数量和覆盖率。例如“已知估算 ¥0.80，另有 2 次费用未知”，不是“总费用 ¥0.80”。主模型、Jev、规则演进、评测调用都纳入可筛选统计，避免只看 planning 少算研发消耗。

无 run_id 的系统调用仍有 call_id、purpose 和 system scope，可出现在全局账单；每次网络尝试只能归入一处汇总，不能同时从 model_call 和 billing_record 重复累加。

## 10. 页面设计

首页保留“Parser 命中率”，另增“输入缓存命中率”“已知模型估算费用”“缓存优惠估算”。后两者按币种分开显示，缺数据注明次数。

运行 Trace 每次调用增加下面的明细卡，数据来自后端统一计算，前端不复制计费规则：

```text
模型用量与费用                              [本地估算]
DeepSeek Flash · CNY · 高峰（依据请求发出时间）

输入 10,000    缓存命中 8,000    未命中 2,000    输出 1,000
输入 Token 命中率 80.0%            统计覆盖 1 / 1 次

缓存输入     8,000 × ¥0.04 / 百万      ¥0.00032
未缓存输入   2,000 × ¥2.00 / 百万      ¥0.00400
输出         1,000 × ¥8.00 / 百万      ¥0.00800
估算合计                                ¥0.01232
同条件全未命中基线 ¥0.02800 · 缓存优惠估算 ¥0.01568

[原始 usage] [价格版本与来源] [时间/日历依据] [导出明细]
```

设置页增加“模型价格”：供应商/账号/币种、模型映射、时间规则、分项单价、日历版本和生效范围。修改先预览一条示例调用的明细，再保存新版本。价格是非敏感配置，API 密钥仍只放 .env。

模型调用页增加日期/供应商/模型/用途过滤，给堆叠用量趋势：命中输入、未命中输入、输出，以及未知用量次数。价格变化在费用趋势上标记版本切换。

## 11. API 设计

- `GET /api/usage/summary?from=...&to=...&provider=...&model=...&purpose=...`：同一规范化聚合结果，时间区间 `[from,to)`。
- `GET /api/billing/calls?limit=...&offset=...`：分页调用与计费明细。
- `GET /api/pricing`：版本列表；`POST /api/pricing/preview`：仅本地计算预览。
- `POST /api/pricing`：创建新版本；`POST /api/pricing/{id}/activate`：本地会话校验后启用未来估算。
- `POST /api/billing/recalculate`：指定历史范围和价格版本，输出追加核算版本，保留原始记录。
- 扩展 `/api/runs/{id}`：增加分币种费用明细、缓存统计和覆盖率；旧字段 cost 过渡保留并标 legacy。

这些是待实施接口。本地会话、Origin 和写标记校验沿用当前机制，价格设置不应通过模型工具自动修改。

## 12. 缓存优化：先测量，再调整

现有 planner 把固定系统规则、已确认记忆和动态消息组合后，再在最后追加 schema。可实验把稳定系统规则、工具/输出 schema 放在动态内容之前，并固定已选 Skill 的顺序和 JSON 序列化。动态时间、message_id、会话工作数据放后面，不为了缓存删掉必要上下文。

这属于单独的提示词版本变更，需要业务回归；移动 system 消息也可能影响生成结果，不应当作纯计费改造顺手上线。衡量优化时比较相同任务的正确性、缓存 Token、延迟和每成功任务成本。

不填充无用 Token 刷命中率、不制造无业务价值的预热调用，不把缓存当成权限隔离。不同来源数据隔离保持原样。后台评测/规则演进未来可选择在空闲时间触发，但日程提醒与用户即时任务不自动推迟；这个调度优化不属于第一版计费统计。

## 13. 验收与测试资料

下列均为**待实现测试清单**，不是本轮已经通过的功能测试：

| 场景 | 预期 |
| --- | --- |
| 全命中 / 全未命中 / 部分命中 | 正确分项算价 |
| 两个命中别名同时出现 | 一致才接受，不重复计算 |
| 命中字段缺失 / 冲突 / 非法计数 | unknown 或 invalid，保留原始证据 |
| 推理 Token 已在输出内 | 不重复加价 |
| JSON 解析失败但 usage 有效 | 记录失败并计费用 |
| 超时、流式中断缺 usage | 未知，不记零；当前非流式不受流式逻辑改动 |
| 08:59:59 / 09:00 / 12:00 / 14:00 / 18:00 | 半开区间边界符合本地约定 |
| 周末、法定节假日、补班周六 | 按供应商日历语义，不套通用工作日判断 |
| 日历不完整、请求跨时段、跨改价 | 区间估算并披露时间依据 |
| 模型别名变化、同名中转商 | 供应商和版本匹配正确 |
| 请求百分比平均与 Token 加权对比 | 100+9900 示例得 1% |
| 更新价表与历史补算 | 历史不覆盖，新 revision 不重复汇总 |
| 主模型 CNY、Jev USD、部分未知 | 分币种已知小计与未知次数 |
| 超过 10000 条调用 | 全量聚合准确，分页不丢总额 |
| 重复事件、恢复、网络重试 | 同一次响应不重复记账，新网络尝试单列 |
| mock 与真实数据并存 | 看板默认仅真实，演示明确标记 |

新增原始响应 fixtures、日期/日历 fixtures、预期 Decimal 明细及评测报告到现有测试归档；真实验证至少覆盖一条明确非零 cache_hit 的响应、一个多调用汇总，并在可获得账单后记录对账差额。未命中不能当作接口错误，也不能强制声称会话重复请求必命中。

## 14. 实施顺序和兼容

1. **采集与规范化**：保存 provider/request/response 身份、UTC 时间及原始 usage；只增加字段，保持原业务流程。
2. **缓存指标**：Trace 展示命中 Token 和加权命中率；缺失数据与 Parser/Jev 分开。
3. **价格快照与计费**：DeepSeek 分时/缓存价表、日历、Decimal 明细、估算范围、分币种汇总。
4. **设置与回算**：规则预览、新版本发布、追加历史核算；再更新 OpenAPI 和前端类型。
5. **测试归档与真实验证**：模拟覆盖边界，独立标明供应商实测，最后才评估缓存前缀优化。

旧 `.env` 两个输入/输出价可转换成 `legacy_flat`，只提供旧口径估算，不虚构缓存折扣和币种。历史供应商、币种或价格生效证据不足时，要求补证据或保持 unknown，不从 DeepSeek 默认 URL 推断过去所有调用都走官方。

当前设计无需 Redis、Docker 或向量库。实现文件集中在 usage.py、billing.py、现有调用采集处与页面；缓存是供应商的缓存，本项目新增的是观测和估算能力。
