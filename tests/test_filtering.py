"""
消息过滤与分类引擎测试。
覆盖：规则分类、白名单/黑名单、关键词过滤、过滤箱、标注集、分类器评估、模型辅助分类。
"""
from unittest.mock import patch, MagicMock
import pytest
from backend.app.modules.mail import filtering
from backend.app.modules.mail.filtering import (
    CATEGORY_AD, CATEGORY_SUBSCRIPTION, CATEGORY_TRANSACTION,
    CATEGORY_TODO, CATEGORY_MANUAL, CATEGORY_LABELS,
    classify_message, should_filter, get_rules, save_rules,
    add_to_filter_box, release_from_filter_box, list_filter_box,
    get_filter_stats, add_labeled_sample, list_labeled_samples,
    evaluate_classifier, classify_with_model, shadow_classify,
    _extract_domain, _match_sender, _match_keywords,
)
from backend.app.core.config import settings


# ========== 辅助函数测试 ==========

class TestHelpers:
    def test_extract_domain(self):
        assert _extract_domain('user@example.com') == 'example.com'
        assert _extract_domain('User Name <user@sub.example.com>') == 'sub.example.com'
        assert _extract_domain('no-at-sign') == ''
        assert _extract_domain('') == ''

    def test_match_sender_exact(self):
        patterns = ['user@example.com']
        assert _match_sender('user@example.com', patterns) is True
        assert _match_sender('other@example.com', patterns) is False

    def test_match_sender_domain(self):
        patterns = ['@example.com']
        assert _match_sender('user@example.com', patterns) is True
        assert _match_sender('user@sub.example.com', patterns) is False
        patterns2 = ['example.com']
        assert _match_sender('user@example.com', patterns2) is True

    def test_match_keywords(self):
        assert _match_keywords('这是一个促销活动', ['促销', '优惠']) == ['促销']
        assert _match_keywords('普通内容', ['促销']) == []
        assert _match_keywords('', ['促销']) == []
        assert _match_keywords('text', []) == []


# ========== 规则分类测试 ==========

class TestRuleClassification:
    def test_classify_ad_multiple_hits(self):
        msg = {'text': '限时优惠！点击立即购买，满减活动', 'sender_id': 'ad@shop.com'}
        cat, conf, ev = classify_message(msg)
        assert cat == CATEGORY_AD
        assert conf >= 0.7

    def test_classify_subscription(self):
        msg = {'text': '技术周报第42期\n本期精选：Python 新特性回顾，往期内容请点击', 'sender_id': 'news@tech.com'}
        cat, conf, ev = classify_message(msg)
        assert cat == CATEGORY_SUBSCRIPTION

    def test_classify_transaction_verification(self):
        msg = {'text': '您的验证码是 123456，5分钟内有效', 'sender_id': 'noreply@bank.com'}
        cat, conf, ev = classify_message(msg)
        assert cat == CATEGORY_TRANSACTION
        assert conf >= 0.8

    def test_classify_transaction_logistics(self):
        msg = {'text': '您的快递已发货，物流单号 SF123456', 'sender_id': 'service@shop.com'}
        cat, conf, ev = classify_message(msg)
        assert cat == CATEGORY_TRANSACTION

    def test_classify_todo_promise(self):
        msg = {'text': '我会在明天下午之前完成报告并发送给你', 'sender_id': 'colleague@work.com'}
        cat, conf, ev = classify_message(msg)
        assert cat == CATEGORY_TODO

    def test_classify_todo_request(self):
        msg = {'text': '请帮我处理一下这个问题，今天下班前完成', 'sender_id': 'boss@work.com'}
        cat, conf, ev = classify_message(msg)
        assert cat == CATEGORY_TODO

    def test_classify_manual_default(self):
        msg = {'text': '今天天气不错，一起吃饭吗？', 'sender_id': 'friend@personal.com'}
        cat, conf, ev = classify_message(msg)
        assert cat == CATEGORY_MANUAL

    def test_all_categories_have_labels(self):
        for cat in [CATEGORY_AD, CATEGORY_SUBSCRIPTION, CATEGORY_TRANSACTION, CATEGORY_TODO, CATEGORY_MANUAL]:
            assert cat in CATEGORY_LABELS
            assert CATEGORY_LABELS[cat]


# ========== 过滤规则测试 ==========

class TestFilterRules:
    def test_get_default_rules(self, db):
        rules = get_rules(db)
        assert rules['version'] == 1
        assert rules['enabled'] is True
        assert CATEGORY_AD in rules['auto_filter_categories']

    def test_save_rules_increments_version(self, db):
        rules1 = save_rules(db, {'whitelist_senders': ['a@b.com']})
        assert rules1['version'] == 2
        rules2 = get_rules(db)
        assert rules2['whitelist_senders'] == ['a@b.com']
        rules3 = save_rules(db, {'whitelist_senders': ['c@d.com']})
        assert rules3['version'] == 3

    def test_whitelist_bypasses_filter(self, db):
        save_rules(db, {'whitelist_senders': ['trusted@example.com'], 'blacklist_senders': ['trusted@example.com']})
        msg = {'text': '限时优惠点击购买', 'sender_id': 'trusted@example.com'}
        should, reason, cat, ev = should_filter(msg, db=db)
        assert should is False
        assert '白名单' in reason

    def test_blacklist_sender(self, db):
        save_rules(db, {'blacklist_senders': ['spam@bad.com']})
        msg = {'text': '正常内容', 'sender_id': 'spam@bad.com'}
        should, reason, cat, ev = should_filter(msg, db=db)
        assert should is True
        assert '黑名单' in reason

    def test_blacklist_domain(self, db):
        save_rules(db, {'blacklist_domains': ['bad.com']})
        msg = {'text': '正常内容', 'sender_id': 'user@bad.com'}
        should, reason, cat, ev = should_filter(msg, db=db)
        assert should is True

    def test_subject_keyword_filter(self, db):
        save_rules(db, {'subject_keywords': ['发票', '报销']})
        msg = {'text': '发票申请\n请查收附件', 'sender_id': 'finance@work.com'}
        should, reason, cat, ev = should_filter(msg, db=db)
        assert should is True
        assert '主题关键词' in reason

    def test_content_keyword_filter(self, db):
        save_rules(db, {'content_keywords': ['赌博', '博彩']})
        msg = {'text': '正常标题\n这里有赌博网站链接', 'sender_id': 'unknown@x.com'}
        should, reason, cat, ev = should_filter(msg, db=db)
        assert should is True

    def test_category_auto_filter(self, db):
        save_rules(db, {'auto_filter_categories': [CATEGORY_AD]})
        msg = {'text': '限时优惠！点击立即购买，满减活动', 'sender_id': 'ad@shop.com'}
        should, reason, cat, ev = should_filter(msg, db=db)
        assert should is True
        assert cat == CATEGORY_AD

    def test_filter_disabled(self, db):
        save_rules(db, {'enabled': False, 'blacklist_senders': ['spam@bad.com']})
        msg = {'text': '内容', 'sender_id': 'spam@bad.com'}
        should, reason, cat, ev = should_filter(msg, db=db)
        assert should is False
        assert '禁用' in reason

    def test_normal_message_passes(self, db):
        save_rules(db, {})
        msg = {'text': '项目进度更新\n本周完成了三个功能', 'sender_id': 'team@work.com'}
        should, reason, cat, ev = should_filter(msg, db=db)
        assert should is False


# ========== 过滤箱测试 ==========

class TestFilterBox:
    def test_add_to_filter_box(self, db):
        msg = {'text': '测试', 'sender_id': 'a@b.com', 'message_id': 'm1'}
        fid = add_to_filter_box(db, msg, '黑名单发件人', category=CATEGORY_AD, evidence=['测试证据'])
        assert fid
        record = db.get(fid)
        assert record['kind'] == 'filter_box'
        assert record['status'] == 'filtered'
        assert record['body']['reason'] == '黑名单发件人'
        assert record['body']['category'] == CATEGORY_AD

    def test_release_from_filter_box(self, db):
        msg = {'text': '测试', 'sender_id': 'a@b.com', 'message_id': 'm1'}
        fid = add_to_filter_box(db, msg, '测试')
        captured = []
        result = release_from_filter_box(db, fid, ingest_func=lambda m: captured.append(m) or 'run-123')
        assert result['status'] == 'released'
        assert result['run_id'] == 'run-123'
        assert len(captured) == 1
        assert db.get(fid)['status'] == 'released'

    def test_release_already_released_fails(self, db):
        msg = {'text': '测试', 'sender_id': 'a@b.com'}
        fid = add_to_filter_box(db, msg, '测试')
        release_from_filter_box(db, fid)
        with pytest.raises(ValueError):
            release_from_filter_box(db, fid)

    def test_list_filter_box(self, db):
        for i in range(3):
            add_to_filter_box(db, {'text': f'测试{i}', 'sender_id': f'a{i}@b.com'}, '测试原因')
        items = list_filter_box(db, limit=10)
        assert len(items) == 3
        assert all(r['status'] == 'filtered' for r in items)

    def test_filter_stats(self, db):
        add_to_filter_box(db, {'text': '广告', 'sender_id': 'ad@x.com'}, '分类过滤', category=CATEGORY_AD)
        add_to_filter_box(db, {'text': '订阅', 'sender_id': 'sub@x.com'}, '分类过滤', category=CATEGORY_SUBSCRIPTION)
        add_to_filter_box(db, {'text': '黑名单', 'sender_id': 'spam@x.com'}, '黑名单发件人')
        stats = get_filter_stats(db)
        assert stats['total'] == 3
        assert stats['by_status']['filtered'] == 3
        assert stats['by_category'][CATEGORY_AD] == 1
        assert stats['by_reason']['黑名单发件人'] == 1


# ========== 标注集与评估测试 ==========

class TestLabeledSamples:
    def test_add_labeled_sample(self, db):
        msg = {'text': '限时优惠点击购买', 'sender_id': 'ad@x.com'}
        sid = add_labeled_sample(db, msg, CATEGORY_AD, source='manual', notes='测试样本')
        assert sid
        record = db.get(sid)
        assert record['body']['expected_category'] == CATEGORY_AD
        assert record['body']['source'] == 'manual'

    def test_labeled_sample_dedupe(self, db):
        msg = {'text': '相同内容', 'sender_id': 'a@b.com'}
        sid1 = add_labeled_sample(db, msg, CATEGORY_AD)
        sid2 = add_labeled_sample(db, msg, CATEGORY_SUBSCRIPTION)
        # 相同指纹应该去重，返回同一个 id
        assert sid1 == sid2

    def test_list_labeled_samples(self, db):
        add_labeled_sample(db, {'text': '广告1'}, CATEGORY_AD)
        add_labeled_sample(db, {'text': '广告2'}, CATEGORY_AD)
        add_labeled_sample(db, {'text': '事务1'}, CATEGORY_TRANSACTION)
        all_samples = list_labeled_samples(db, limit=10)
        assert len(all_samples) == 3
        ad_samples = list_labeled_samples(db, category=CATEGORY_AD, limit=10)
        assert len(ad_samples) == 2

    def test_evaluate_classifier(self, db):
        # 添加一些标注样本
        add_labeled_sample(db, {'text': '限时优惠点击购买满减', 'sender_id': 'ad@x.com'}, CATEGORY_AD)
        add_labeled_sample(db, {'text': '您的验证码是123456', 'sender_id': 'bank@x.com'}, CATEGORY_TRANSACTION)
        add_labeled_sample(db, {'text': '我会明天完成报告', 'sender_id': 'colleague@x.com'}, CATEGORY_TODO)
        result = evaluate_classifier(db)
        assert result['total'] == 3
        assert result['correct'] >= 0
        assert 0 <= result['accuracy'] <= 1
        assert 'confusion_matrix' in result
        assert 'false_positives' in result
        assert 'false_negatives' in result


# ========== 模型辅助分类测试 ==========

class TestModelClassification:
    def test_classify_with_model_not_configured(self, db, monkeypatch):
        monkeypatch.setattr(settings, 'model_api_key', '')
        monkeypatch.setattr(settings, 'model_name', '')
        cat, conf, reason, raw = classify_with_model({'text': '测试'})
        assert cat is None
        assert '未配置' in reason

    def test_classify_with_model_mock(self, db, monkeypatch):
        monkeypatch.setattr(settings, 'model_api_key', 'test-key')
        monkeypatch.setattr(settings, 'model_name', 'test-model')
        monkeypatch.setattr(settings, 'model_base_url', 'https://api.example.com/v1')
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'choices': [{'message': {'content': '{"category": "ad", "confidence": 0.9, "reason": "包含促销关键词"}'}}]
        }
        mock_response.raise_for_status = MagicMock()
        with patch('backend.app.modules.mail.filtering.httpx.Client') as mock_client:
            mock_client.return_value.__enter__.return_value.post.return_value = mock_response
            cat, conf, reason, raw = classify_with_model({'text': '限时优惠'}, db=db)
        assert cat == CATEGORY_AD
        assert conf == 0.9

    def test_shadow_classify(self, db, monkeypatch):
        monkeypatch.setattr(settings, 'model_api_key', 'test-key')
        monkeypatch.setattr(settings, 'model_name', 'test-model')
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'choices': [{'message': {'content': '{"category": "ad", "confidence": 0.85, "reason": "test"}'}}]
        }
        mock_response.raise_for_status = MagicMock()
        with patch('backend.app.modules.mail.filtering.httpx.Client') as mock_client:
            mock_client.return_value.__enter__.return_value.post.return_value = mock_response
            msg = {'text': '限时优惠点击购买', 'sender_id': 'ad@x.com', 'message_id': 'm1'}
            result = shadow_classify(db, msg)
        assert result['rule_category'] == CATEGORY_AD
        assert result['model_category'] == CATEGORY_AD
        assert result['agreement'] is True
        # 验证影子记录已保存
        shadows = db.list('classification_shadow')
        assert len(shadows) == 1
