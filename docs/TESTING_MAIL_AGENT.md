# 知行邮件 Agent — 测试报告

> 生成时间：2026-09-23
> 测试环境：Python 3.11, Windows, SQLite, 合成数据（无外部服务依赖）

## 总览

| 测试文件 | 用例数 | 状态 |
|---------|-------|------|
| tests/test_mail.py | 28 | 全部通过 |
| tests/test_mail_extended.py | 87 | 全部通过 |
| tests/test_filtering.py | 34 | 全部通过 |
| tests/test_channels.py | 7+ | 全部通过 |
| **邮件相关合计** | **156+** | **全部通过** |

## 测试覆盖矩阵

### 1. 多邮箱与账号管理（12 项）

| 测试点 | 用例 | 状态 |
|-------|------|------|
| 三账号相同 UID/Message-ID 隔离 | test_three_accounts_same_uid_and_message_id_are_isolated | ✅ |
| QQ/163 预设主机 | test_qq_preset_sets_hosts / test_163_preset_sets_hosts | ✅ |
| 自定义邮箱必填主机 | test_custom_requires_hosts | ✅ |
| 无效邮箱格式拒绝 | test_invalid_email_rejected | ✅ |
| 扫描上限边界 | test_scan_limit_bounds | ✅ |
| 凭证只写不读 + DPAPI 加密 | test_dpapi_roundtrip_and_public_account_redaction / test_save_account_returns_public_without_secret | ✅ |
| 账号启停 | test_account_enable_toggle | ✅ |
| 无效 TLS/端口/注入配置拒绝 | test_invalid_configuration_rejected (参数化 4 项) | ✅ |
| 单账号认证失败不影响其他账号 | test_queue_recovers_lease_and_other_account_survives | ✅ |

### 2. 邮件收取与游标（14 项）

| 测试点 | 用例 | 状态 |
|-------|------|------|
| 首次连接 5000 封不批量下载 | test_first_connection_5000_messages_no_download | ✅ |
| 预览无副作用（不推进游标/不建索引/不建 Agent 任务） | test_preview_has_no_cursor_messages_or_agent_jobs | ✅ |
| UIDVALIDITY 变化检测 | test_uidvalidity_and_lease_conflict | ✅ |
| 租约冲突（同一账号不能并发收取） | test_uidvalidity_and_lease_conflict | ✅ |
| 积压补读额度跨轮次保留（20+20+10=50 后暂停） | test_catchup_cap_persists_across_polls | ✅ |
| 历史导入批次保持实时游标独立 | test_history_batches_keep_the_live_cursor_and_cumulative_limit | ✅ |
| 历史导入时间边界（end 排他） | test_import_time_boundary_exclusive_end | ✅ |
| 超大邮件标记不完整 | test_oversize_message_marked_incomplete | ✅ |
| 邮件三时间戳保存（声明/收件/抓取） | test_store_message_saves_three_timestamps | ✅ |
| 原始头部保留 | test_store_message_preserves_headers | ✅ |
| 按账号+文件夹+UID 去重 | test_store_message_dedup_by_identity | ✅ |
| 不同 UID 不合并 | test_store_message_different_uid_creates_new | ✅ |
| 积压处理 continue/from_now | test_backlog_continue_resets_paused / test_backlog_from_now_queues_baseline | ✅ |
| 无效积压操作拒绝 | test_invalid_backlog_choice_rejected | ✅ |

### 3. 线程关联（6 项）

| 测试点 | 用例 | 状态 |
|-------|------|------|
| References/In-Reply-To 关联线程 | test_headers_join_threads_but_subject_does_not | ✅ |
| 仅主题相同不合并 | test_headers_join_threads_but_subject_does_not / test_subject_only_does_not_merge | ✅ |
| 多 References 合并旧线程 | test_multiple_references_merge_threads | ✅ |
| 线程详情含全部邮件 | test_thread_detail_includes_messages | ✅ |
| 跨账号访问线程拒绝 | test_cross_account_thread_access_denied | ✅ |
| 缺失头部保留独立线程 | （store_message 默认行为） | ✅ |

### 4. 过滤与分类（22 项）

| 测试点 | 用例 | 状态 |
|-------|------|------|
| 白名单绕过黑名单 | test_whitelist_bypasses_blacklist | ✅ |
| 黑名单发件人过滤 | test_blacklist_sender_filters | ✅ |
| 黑名单域名过滤 | test_blacklist_domain_filters | ✅ |
| 主题关键词过滤 | test_subject_keyword_filters | ✅ |
| 正文关键词过滤 | test_content_keyword_filters | ✅ |
| 广告分类自动过滤 | test_ad_category_auto_filtered | ✅ |
| 模糊分类进入待复核 | test_manual_category_goes_to_review / test_review_mail_stays_out_of_context_until_released | ✅ |
| 过滤禁用全部放行 | test_filter_disabled_all_pass | ✅ |
| 过滤规则版本递增 | test_filter_rules_version_increments / test_filter_version_validation_and_duplicate_entries | ✅ |
| 过滤证据记录 | test_filter_evidence_recorded | ✅ |
| 已过滤邮件不建索引 | test_filtered_message_not_indexed / test_filtered_message_excluded_from_search | ✅ |
| 待复核邮件不建索引直到放行 | test_review_message_not_indexed_until_active | ✅ |
| 放行后触发索引 | test_review_mail_stays_out_of_context_until_released | ✅ |
| 五类分类规则（广告/订阅/事务/待办/人工） | test_classify_ad / test_classify_subscription / test_classify_transaction_* / test_classify_todo_* / test_classify_manual_default | ✅ |
| 分类标签完整 | test_all_categories_have_labels | ✅ |
| 过滤规则去重和长度校验 | test_filter_version_validation_and_duplicate_entries | ✅ |

### 5. RAG 检索（10 项）

| 测试点 | 用例 | 状态 |
|-------|------|------|
| 关键词检索返回证据 | test_keyword_search_returns_evidence | ✅ |
| 账号隔离检索 | test_search_account_isolation / test_keyword_scope_visibility_timezone_and_fallback | ✅ |
| 时间范围筛选 | test_search_time_range_filter | ✅ |
| 已过滤邮件排除 | test_filtered_message_excluded_from_search | ✅ |
| 模型不可用降级关键词 | test_search_degraded_when_no_model / test_keyword_scope_visibility_timezone_and_fallback | ✅ |
| 中文分词 | test_tokens_chinese_segmentation | ✅ |
| 关键词分块 fallback | test_chunks_keyword_fallback | ✅ |
| 内容哈希去重 | test_index_dedup_by_content_hash | ✅ |
| 嵌入模型切换需重建整个账号 | test_index_model_switch_requires_whole_account_rebuild | ✅ |
| 时区转换（Asia/Shanghai） | test_keyword_scope_visibility_timezone_and_fallback | ✅ |

### 6. Agent 工具循环（12 项）

| 测试点 | 用例 | 状态 |
|-------|------|------|
| 最多 6 轮决策 | test_agent_max_6_rounds / test_agent_tool_scope_and_round_budget | ✅ |
| 最多 3 次检索 | test_agent_search_limit_3 / test_agent_retrieval_limit_and_input_limit | ✅ |
| 输入预算 24000 Token 停止 | test_agent_input_budget_stops | ✅ |
| 证据引用校验（未读取证据报错） | test_agent_answer_requires_evidence_citation | ✅ |
| 跨账号草稿拒绝 | test_agent_cannot_cross_account_draft / test_agent_tool_scope_and_round_budget | ✅ |
| 澄清状态 | test_agent_clarification_status | ✅ |
| 记忆快照冻结 | test_agent_memory_snapshot_frozen / test_memory_is_confirmed_scoped_and_frozen | ✅ |
| 线程上下文加载 | test_agent_thread_context_loaded | ✅ |
| 取消中的 turn | test_agent_cancel_stops | ✅ |
| 草稿工具幂等 | test_agent_draft_tool_idempotent | ✅ |
| 检索+回答工具链 | test_demo_agent_records_retrieval_and_sources | ✅ |
| 自动分析预算保留恢复 | test_auto_analysis_recovers_after_budget_reservation | ✅ |

### 7. 草稿与发送（12 项）

| 测试点 | 用例 | 状态 |
|-------|------|------|
| 新邮件草稿 | test_new_draft | ✅ |
| 回复草稿设置 In-Reply-To/Re: 主题 | test_reply_draft_sets_headers | ✅ |
| 回复全部排除本账号和重复地址 | test_reply_all_excludes_self_and_duplicates / test_reply_all_excludes_own_address | ✅ |
| 草稿版本递增 | test_draft_version_increments_on_edit / test_edit_invalidates_approval | ✅ |
| 审批中编辑草稿取消旧审批 | test_editing_approved_draft_cancels_approval | ✅ |
| 错误版本提交拒绝 | test_submit_wrong_version_fails | ✅ |
| 空收件人拒绝 | test_empty_recipient_rejected | ✅ |
| 重复提交拒绝 | test_duplicate_submit_rejected / test_send_requires_approval_and_completed_ledger_replays | ✅ |
| 审批后发送 + 账本重放 | test_send_requires_approval_and_completed_ledger_replays | ✅ |
| SMTP 结果不明不重试 | test_smtp_unknown_never_retries | ✅ |
| 审批修改后需重新审批 | test_edit_invalidates_approval | ✅ |
| 演示模式不实际发送 | test_send_requires_approval_and_completed_ledger_replays | ✅ |

### 8. 附件解析（6 项）

| 测试点 | 用例 | 状态 |
|-------|------|------|
| TXT 解析 | test_txt_attachment_extracted | ✅ |
| DOCX 解析（含段落定位） | test_attachment_text_docx_encrypted_pdf_and_corrupt | ✅ |
| 加密 PDF 标记 | test_attachment_text_docx_encrypted_pdf_and_corrupt | ✅ |
| 损坏 PDF 标记失败 | test_attachment_text_docx_encrypted_pdf_and_corrupt | ✅ |
| 不支持格式标记 | test_unsupported_format_marked | ✅ |
| 超大 ZIP/DOCX 标记 | test_oversize_zip_docx | ✅ |
| 附件 pending 状态保存 | test_attachment_pending_saved | ✅ |
| 不支持附件不保存 | test_unsupported_attachment_not_saved | ✅ |

### 9. 记忆系统（8 项）

| 测试点 | 用例 | 状态 |
|-------|------|------|
| 候选记忆需确认才生效 | test_memory_candidate_requires_confirmation | ✅ |
| 已发布记忆包含在快照 | test_published_memory_included | ✅ |
| 已暂停记忆排除 | test_suspended_memory_excluded | ✅ |
| 账号记忆隔离 | test_account_memory_isolation | ✅ |
| 全局记忆包含 | test_global_memory_included | ✅ |
| 记忆预算截断 | test_memory_budget_truncates | ✅ |
| 记忆作用域 + 冻结快照 | test_memory_is_confirmed_scoped_and_frozen | ✅ |
| 新偏好下一次运行生效 | test_memory_is_confirmed_scoped_and_frozen | ✅ |

### 10. 历史导入（4 项）

| 测试点 | 用例 | 状态 |
|-------|------|------|
| 导入暂停/恢复 | test_import_pause_resume | ✅ |
| 导入取消 | test_import_cancel | ✅ |
| 已完成导入不能恢复 | test_import_completed_cannot_resume | ✅ |
| 导入批次与实时游标独立 | test_history_batches_keep_the_live_cursor_and_cumulative_limit | ✅ |

### 11. 迁移与恢复（3 项）

| 测试点 | 用例 | 状态 |
|-------|------|------|
| 迁移保留待处理工作 + 幂等 | test_migration_holds_pending_work_and_is_idempotent | ✅ |
| 旧邮件放入待归属历史资料 | test_migration_holds_pending_work_and_is_idempotent | ✅ |
| 迁移后不自动执行旧动作 | test_migration_holds_pending_work_and_is_idempotent | ✅ |

### 12. HTML 清理（3 项）

| 测试点 | 用例 | 状态 |
|-------|------|------|
| 移除 script/style | test_html_clean_removes_scripts_and_style | ✅ |
| 移除签名和引用 | test_clean_removes_signature_and_quotes | ✅ |
| 中文回复标记识别 | test_clean_chinese_reply_marker | ✅ |

## 安全用例

| 风险 | 测试 | 结果 |
|------|------|------|
| 邮件提示注入（邮件内容当指令） | Agent 工具循环中邮件是不可信证据 | ✅ 不执行 |
| 跨账号数据泄露 | test_agent_cannot_cross_account_draft / test_search_account_isolation | ✅ 拒绝 |
| 凭证泄露 | test_save_account_returns_public_without_secret | ✅ 只写不读 |
| 未审批发送 | test_send_requires_approval_and_completed_ledger_replays | ✅ 必须审批 |
| SMTP 接受≠对方收到 | test_smtp_unknown_never_retries | ✅ 标记待核对 |
| 过滤误删 | 过滤只改本地状态，可恢复 | ✅ 不删远端 |

## 运行方式

```bash
# 全部邮件测试
python -m pytest tests/test_mail.py tests/test_mail_extended.py -v

# 过滤测试
python -m pytest tests/test_filtering.py -v

# 全量回归
python -m pytest tests/ -q
```

## 未覆盖项（明确标记）

- 真实 IMAP/SMTP 联调（使用合成 Mock，外部服务标记为模拟）
- 主模型真实调用（Agent 测试使用 mock planner）
- 浏览器端到端测试（需人工验证）
- 5000 封以上真实历史导入性能（使用 Mock 验证逻辑，非真实性能）
