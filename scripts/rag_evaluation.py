"""RAG 评测：关键词 vs 混合检索+重排序，消融对比。
使用合成邮件数据，无需外部模型服务。
运行: $env:ZHIXING_WORKER='1'; python scripts/rag_evaluation.py
"""
import argparse, json, time, os, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage

ROOT=Path(__file__).resolve().parent.parent


def prepare_output_dir():
    parser=argparse.ArgumentParser(description='使用合成邮件对照关键词与混合检索；每次结果写入独立归档目录。')
    parser.add_argument('--output-dir',help='评测归档目录；必须是新目录，避免覆盖旧结果。')
    options=parser.parse_args()
    folder=Path(options.output_dir).resolve() if options.output_dir else ROOT/'artifacts'/'rag-evaluations'/datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    folder.mkdir(parents=True,exist_ok=False)
    (folder/'backend-data').mkdir(parents=True)
    os.environ['ZHIXING_DATA_DIR']=str(folder/'backend-data')
    os.environ['ZHIXING_MODE']='demo';os.environ['ZHIXING_MAIL_WORKER']='1';os.environ['ZHIXING_DISABLE_MAIL_NETWORK']='1'
    return folder


OUTPUT_DIR=prepare_output_dir() if __name__=='__main__' else None
sys.path.insert(0,str(ROOT))
os.environ.setdefault('ZHIXING_MAIL_WORKER','1')

from backend.app.modules.mail.repository import initialize, save_account, rows
from backend.app.modules.mail.schemas import MailAccount
from backend.app.modules.mail.ingestion import store_message
from backend.app.modules.mail.retrieval import index_message, search, load_models, model_version


EVAL_QUERIES = [
    {'q': '实验报告截止时间', 'expected_senders': ['teacher@example.com'], 'category': 'exact'},
    {'q': '导师要求修改的内容', 'expected_senders': ['teacher@example.com'], 'category': 'exact'},
    {'q': '仪器参数设置', 'expected_senders': ['lab@example.com'], 'category': 'exact'},
    {'q': '什么时候交作业', 'expected_senders': ['teacher@example.com'], 'category': 'semantic'},
    {'q': '项目进度安排', 'expected_senders': ['boss@example.com'], 'category': 'semantic'},
    {'q': '报销流程', 'expected_senders': ['hr@example.com'], 'category': 'semantic'},
    {'q': '实验和会议都有哪些截止日期', 'expected_senders': ['teacher@example.com', 'boss@example.com'], 'category': 'multi'},
    {'q': '需要我回复确认的事项', 'expected_senders': ['teacher@example.com', 'colleague@example.com'], 'category': 'multi'},
    {'q': '不是广告的重要通知', 'expected_senders': ['hr@example.com', 'boss@example.com'], 'category': 'negation'},
    {'q': '下周的安排', 'expected_senders': ['boss@example.com'], 'category': 'temporal'},
    {'q': '上个月的报销', 'expected_senders': ['hr@example.com'], 'category': 'temporal'},
    {'q': '导师发来的所有要求', 'expected_senders': ['teacher@example.com'], 'category': 'sender'},
    {'q': '同事的协作请求', 'expected_senders': ['colleague@example.com'], 'category': 'sender'},
    {'q': 'PDF 文档里的内容', 'expected_senders': ['lab@example.com'], 'category': 'attachment'},
    {'q': '谁承诺了什么时间完成', 'expected_senders': ['colleague@example.com', 'boss@example.com'], 'category': 'todo'},
    {'q': '重要的事', 'expected_senders': ['teacher@example.com', 'boss@example.com'], 'category': 'vague'},
    {'q': 'GPU 显存不足怎么解决', 'expected_senders': ['lab@example.com'], 'category': 'technical'},
    {'q': '数据预处理步骤', 'expected_senders': ['lab@example.com'], 'category': 'technical'},
    {'q': '关于论文修改的所有讨论', 'expected_senders': ['teacher@example.com', 'colleague@example.com'], 'category': 'thread'},
    {'q': '经费预算金额', 'expected_senders': ['boss@example.com'], 'category': 'numeric'},
]

SYNTHETIC_MAILS = [
    {'from': 'teacher@example.com', 'subject': '实验报告要求', 'body': '请在本周五下午5点前提交实验报告，包含实验目的、方法、结果和结论。报告需要使用 LaTeX 排版。', 'mid': '<t1@example.com>'},
    {'from': 'teacher@example.com', 'subject': '论文修改意见', 'body': '你的论文第三章需要补充对比实验，第四章的数据分析不够深入。请在两周内完成修改并提交新版本。参考文献格式需要统一为 APA。', 'mid': '<t2@example.com>'},
    {'from': 'teacher@example.com', 'subject': '组会时间变更', 'body': '本周组会改为周四下午2点，地点在实验室301。请提前准备好进度汇报PPT。', 'mid': '<t3@example.com>'},
    {'from': 'boss@example.com', 'subject': '项目进度汇报', 'body': '下周一上午10点进行项目进度汇报，请准备好当前进展、遇到的问题和下周计划。项目预算目前使用了60%，请注意控制成本。', 'mid': '<b1@example.com>'},
    {'from': 'boss@example.com', 'subject': '下周工作安排', 'body': '下周主要任务：1.完成用户调研报告 2.与产品团队对接需求 3.准备技术方案评审。请在周五前提交周报。', 'mid': '<b2@example.com>'},
    {'from': 'boss@example.com', 'subject': '经费审批', 'body': '本次项目经费预算为50000元，其中设备采购30000元，差旅10000元，其他10000元。请提交详细的经费使用计划。', 'mid': '<b3@example.com>'},
    {'from': 'lab@example.com', 'subject': '仪器使用说明', 'body': 'GPU服务器的显存为24GB，运行大模型时可能出现显存不足。解决方案：1.使用梯度累积 2.降低batch size 3.启用混合精度训练。数据预处理步骤包括清洗、分词、向量化。', 'mid': '<l1@example.com>'},
    {'from': 'lab@example.com', 'subject': '实验数据共享', 'body': '实验数据已上传到共享服务器，包含原始数据和预处理后的特征。PDF文档中详细说明了数据采集方法和标注规范。', 'mid': '<l2@example.com>'},
    {'from': 'lab@example.com', 'subject': '设备维护通知', 'body': '本周五下午进行服务器维护，期间GPU集群不可用。请提前保存好实验进度。', 'mid': '<l3@example.com>'},
    {'from': 'hr@example.com', 'subject': '报销流程更新', 'body': '从本月起，报销需要在系统中提交电子发票，审批流程为：部门主管→财务→出纳。上个月的报销请在本月15日前完成提交。', 'mid': '<h1@example.com>'},
    {'from': 'hr@example.com', 'subject': '培训通知', 'body': '下周三下午2点进行新员工培训，内容包括公司制度、安全规范和系统使用。请准时参加。', 'mid': '<h2@example.com>'},
    {'from': 'colleague@example.com', 'subject': '协作请求', 'body': '我负责的模块预计下周二完成，需要你帮忙做代码review。请确认你是否有时间，我们可以约个时间讨论。论文修改部分我也可以帮忙看一下。', 'mid': '<c1@example.com>'},
    {'from': 'colleague@example.com', 'subject': '数据同步问题', 'body': '我们两个模块的数据接口需要对齐，我已经更新了文档，请查看并确认是否有问题。', 'mid': '<c2@example.com>'},
    {'from': 'ad@shop.com', 'subject': '限时优惠活动', 'body': '点击立即购买，满减活动，免费领取优惠券。', 'mid': '<a1@example.com>'},
    {'from': 'news@tech.com', 'subject': '技术周报第42期', 'body': '本期精选：Python新特性回顾，AI最新进展。往期内容可在官网查看。', 'mid': '<a2@example.com>'},
    {'from': 'noreply@bank.com', 'subject': '验证码', 'body': '您的验证码是123456，5分钟内有效。', 'mid': '<n1@example.com>'},
    {'from': 'service@shop.com', 'subject': '快递发货通知', 'body': '您的订单已发货，物流单号SF123456，预计3天内送达。', 'mid': '<n2@example.com>'},
]


def setup_db(db):
    initialize(db)
    aid = save_account(db, MailAccount(name='评测账号', address='user@example.com', enabled=True))['id']
    from backend.app.modules.mail.filtering import DEFAULT_RULES
    db.insert('setting', {**DEFAULT_RULES, 'whitelist_senders': ['teacher@example.com', 'boss@example.com', 'lab@example.com', 'hr@example.com', 'colleague@example.com']}, id='mail-filter:' + aid)
    return aid


def seed_mails(db, aid):
    base = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    mids = []
    for i, m in enumerate(SYNTHETIC_MAILS):
        msg = EmailMessage()
        msg['From'] = m['from']
        msg['To'] = 'user@example.com'
        msg['Subject'] = m['subject']
        msg['Message-ID'] = m['mid']
        msg.set_content(m['body'])
        received = (base + timedelta(days=i)).isoformat()
        mid = store_message(db, aid, '100', i + 1, msg.as_bytes(), received)
        mids.append(mid)
    return mids


def evaluate_search(db, aid, force_keyword=False):
    """运行评测。force_keyword=True 时 mock 模型不可用，纯关键词检索。"""
    import backend.app.modules.mail.retrieval as rag
    results = []
    original_load = rag.load_models
    if force_keyword:
        def fake_load(*a, **k): raise RuntimeError('mock: model unavailable')
        rag.load_models = fake_load
    try:
        for q in EVAL_QUERIES:
            t0 = time.time()
            try:
                r = search(db, {'account_ids': [aid], 'query': q['q']})
                latency = (time.time() - t0) * 1000
                hit_senders = set()
                for e in r['evidence']:
                    msg = db.get(e['message_id'])
                    if msg:
                        hit_senders.add(msg['body'].get('sender', ''))
                expected = set(q['expected_senders'])
                recall = len(hit_senders & expected) / len(expected) if expected else 0
                precision = len(hit_senders & expected) / len(hit_senders) if hit_senders else 0
                results.append({
                    'query': q['q'], 'category': q['category'],
                    'mode': r.get('mode', 'keyword'), 'degraded': r.get('degraded', False),
                    'evidence_count': len(r['evidence']), 'recall': recall, 'precision': precision,
                    'latency_ms': round(latency, 1),
                    'hit_senders': list(hit_senders), 'expected_senders': list(expected),
                })
            except Exception as e:
                results.append({'query': q['q'], 'category': q['category'], 'error': str(e), 'recall': 0, 'precision': 0, 'latency_ms': 0})
    finally:
        rag.load_models = original_load
    return results


def compute_metrics(results):
    valid = [r for r in results if 'error' not in r]
    if not valid:
        return {'total': len(results), 'errors': len(results)}
    overall = {
        'total': len(results), 'errors': len(results) - len(valid),
        'avg_recall': round(sum(r['recall'] for r in valid) / len(valid), 3),
        'avg_precision': round(sum(r['precision'] for r in valid) / len(valid), 3),
        'avg_latency_ms': round(sum(r['latency_ms'] for r in valid) / len(valid), 1),
        'recall_at_1': round(sum(1 for r in valid if r['recall'] >= 1.0) / len(valid), 3),
        'degraded_count': sum(1 for r in valid if r.get('degraded')),
    }
    by_category = {}
    for r in valid:
        cat = r['category']
        if cat not in by_category:
            by_category[cat] = {'count': 0, 'recall_sum': 0, 'precision_sum': 0, 'latency_sum': 0}
        by_category[cat]['count'] += 1
        by_category[cat]['recall_sum'] += r['recall']
        by_category[cat]['precision_sum'] += r['precision']
        by_category[cat]['latency_sum'] += r['latency_ms']
    for cat in by_category:
        c = by_category[cat]
        by_category[cat] = {
            'count': c['count'],
            'avg_recall': round(c['recall_sum'] / c['count'], 3),
            'avg_precision': round(c['precision_sum'] / c['count'], 3),
            'avg_latency_ms': round(c['latency_sum'] / c['count'], 1),
        }
    return {'overall': overall, 'by_category': by_category}


def main():
    from backend.app.persistence.store import Store
    folder=OUTPUT_DIR
    os.environ.pop('ZHIXING_DISABLE_LOCAL_MODELS', None)
    database=folder/'backend-data'/'eval.db';database.parent.mkdir(parents=True,exist_ok=True)
    db = Store(database)

    print('=== 知行 RAG 评测 ===')
    print(f'评测查询: {len(EVAL_QUERIES)} 条, 合成邮件: {len(SYNTHETIC_MAILS)} 封')
    print()

    aid = setup_db(db)
    mids = seed_mails(db, aid)
    print(f'已导入 {len(mids)} 封邮件')

    print('建立索引...')
    indexed = sum(1 for mid in mids if index_message(db, mid).get('chunks', 0) > 0)
    print(f'已索引 {indexed} 封邮件')
    print()

    models_available = False
    try:
        load_models()
        models_available = True
        print(f'模型已加载: {model_version()}')
    except Exception as e:
        print(f'模型不可用，仅运行关键词模式: {e}')
    print()

    all_results = {}

    print('--- 关键词检索（降级模式） ---')
    kw = evaluate_search(db, aid, force_keyword=True)
    kw_m = compute_metrics(kw)
    print(f"Recall={kw_m['overall']['avg_recall']} Precision={kw_m['overall']['avg_precision']} Latency={kw_m['overall']['avg_latency_ms']}ms")
    all_results['keyword'] = {'metrics': kw_m, 'details': kw}
    print()

    if models_available:
        print('--- 混合检索 + 重排序 ---')
        hy = evaluate_search(db, aid, force_keyword=False)
        hy_m = compute_metrics(hy)
        print(f"Recall={hy_m['overall']['avg_recall']} Precision={hy_m['overall']['avg_precision']} Latency={hy_m['overall']['avg_latency_ms']}ms")
        all_results['hybrid'] = {'metrics': hy_m, 'details': hy}
        print()

        print('=== 分类对比（关键词 → 混合） ===')
        print(f"{'类别':<12} {'关键词R':<10} {'混合R':<10} {'Δ':<8} {'关键词P':<10} {'混合P':<10}")
        for cat in sorted(kw_m['by_category'].keys()):
            kr = kw_m['by_category'][cat]['avg_recall']
            hr = hy_m['by_category'].get(cat, {}).get('avg_recall', 0)
            kp = kw_m['by_category'][cat]['avg_precision']
            hp = hy_m['by_category'].get(cat, {}).get('avg_precision', 0)
            print(f"{cat:<12} {kr:<10} {hr:<10} {hr-kr:+.3f}   {kp:<10} {hp:<10}")

    out = folder / 'results.json'
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f'\n结果已保存: {out}')
    db.engine.dispose()


if __name__ == '__main__':
    main()
