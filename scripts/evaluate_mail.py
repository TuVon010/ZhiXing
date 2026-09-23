"""Reproducible synthetic mail retrieval benchmark with REAL local models.

Labels are deterministic scenario-author labels, not human/model adjudication.
No external LLM is called; generative quality must not be inferred from this run.
"""
import os
import sys
import json
import math
import time
import hashlib
import statistics
from pathlib import Path
from datetime import datetime
from email.message import EmailMessage

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['ZHIXING_MAIL_WORKER']='1'
os.environ.pop('ZHIXING_DISABLE_LOCAL_MODELS',None)
os.environ['ZHIXING_MODE']='demo'
os.environ['ZHIXING_JEV_MODE']='off'


def cases():
    topics=[('实验','补充消融对照实验','还缺哪些对照结果'),('合同','核对合同付款条款','付款约定需要检查什么'),
            ('会议','准备周会演示材料','开会要提前准备什么'),('采购','确认设备采购清单','需要买哪些仪器'),
            ('报销','提交差旅发票原件','出差费用如何报销'),('论文','修改论文相关工作章节','文献综述需要怎么调整'),
            ('招聘','准备技术面试项目介绍','面试需要准备哪些内容'),('发布','完成上线前回归测试','版本发布前要检查什么'),
            ('课程','提交课程期末报告','期末作业是什么'),('安全','轮换测试环境访问令牌','测试凭证需要如何维护')]
    output=[]
    for project in range(20):
        split='optimization' if project<10 else 'holdout'
        for index,(topic,action,paraphrase) in enumerate(topics):
            code=f'项目{project+1:02d}';ident=f'mail-{project:02d}-{index}'
            output.append({'id':ident,'split':split,'thread_key':ident,'account':project%3,
                'subject':f'{code} {topic}安排','messages':[f'{code}：请在周三前{action}。',f'更正：{code}的{topic}事项改为周五截止。具体任务仍是{action}。请回复确认；勿重复创建旧截止时间的任务。'],
                'query':f'{code}，{paraphrase}，最新截止时间是哪天？','expected_deadline':'周五','expected_action':action,
                'relevance':[1,2],'send_requires_approval':True,'label_source':'synthetic_scenario_author'})
    return output


def main():
    output=ROOT/'artifacts'/'mail-evaluations'/datetime.now().strftime('%Y%m%d-%H%M%S')
    output.mkdir(parents=True,exist_ok=False)
    os.environ['ZHIXING_DATA_DIR']=str(output/'data')
    from backend.db import Store
    from backend.mail_store import initialize,save_account
    from backend.mail_models import MailAccount
    from backend.mail_ingest import store_message
    from backend.mail_rag import load_models,index_message,search,model_manifest,rebuild_account
    from backend.filtering import DEFAULT_RULES
    data=cases();(output/'cases.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Evidence:',output,flush=True)
    manifest={'verification':'real_local_embedding_and_reranker','external_mail_verified':False,'main_model_verified':False,
              'dataset_cases':200,'split_counts':{'optimization':100,'holdout':100},'models':model_manifest(),
              'cost':None,'tokens':None,'cache_hit_rate':None,'generation_metrics':None,
              'limitations':['模板生成的合成场景，不能替代真实邮件质量评测','未运行主模型，引用支持率、任务完成率和记忆/自主决策消融未测量']}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    load_models() # Fail loudly; never silently count keyword fallback as real RAG.
    db=Store(output/'data'/'benchmark.db');initialize(db)
    accounts=[]
    for i in range(3):
        aid=save_account(db,MailAccount(name=f'合成账号{i}',address=f'synthetic{i}@qq.com'))['id'];accounts.append(aid)
        db.insert('setting',{**DEFAULT_RULES,'whitelist_senders':['author@example.com']},id='mail-filter:'+aid)
    expected={}
    with (output/'index.jsonl').open('w',encoding='utf-8') as log:
        for ci,case in enumerate(data):
            relevance={}
            for mi,content in enumerate(case['messages']):
                msg=EmailMessage();msg['From']='author@example.com';msg['To']=f'synthetic{case["account"]}@qq.com';msg['Subject']=case['subject'];msg['Message-ID']=f'<{case["id"]}-{mi}@synthetic.local>'
                if mi:msg['In-Reply-To']=f'<{case["id"]}-0@synthetic.local>'
                msg.set_content(content)
                mid=store_message(db,accounts[case['account']],'evaluation',ci*2+mi+1,msg.as_bytes(),'2026-09-22T10:00:00+00:00')
                result=index_message(db,mid)
                if result['degraded']:raise RuntimeError('Real indexing degraded: '+str(result))
                log.write(json.dumps({'case':case['id'],'message_id':mid,**result},ensure_ascii=False)+'\n');log.flush()
                relevance[mid]=case['relevance'][mi]
            expected[case['id']]=relevance
            if ci%20==0:print('Indexed',ci+1,'/200',flush=True)
    summary={};results=[]
    with (output/'rankings.jsonl').open('w',encoding='utf-8') as log:
        for ci,case in enumerate(data):
            relevant=expected[case['id']]
            for mode in ['keyword','vector','fusion','hybrid']:
                result=search(db,{'account_ids':[accounts[case['account']]],'query':case['query'],'mode':mode})
                if result['degraded']:raise RuntimeError('Real search degraded: '+str(result['reason']))
                retrieved=list(dict.fromkeys(e['message_id'] for e in result['evidence']))
                recall=len(set(retrieved)&set(relevant))/len(relevant)
                dcg=sum((2**relevant.get(mid,0)-1)/math.log2(i+2) for i,mid in enumerate(retrieved))
                ideal=sum((2**grade-1)/math.log2(i+2) for i,grade in enumerate(sorted(relevant.values(),reverse=True)))
                row={'case':case['id'],'split':case['split'],'mode':mode,'recall_at_8':recall,'ndcg_at_8':dcg/ideal,'retrieval':result}
                results.append(row);log.write(json.dumps(row,ensure_ascii=False)+'\n');log.flush()
            if ci%10==0:print('Evaluated',ci+1,'/200',flush=True)
    for split in ['optimization','holdout']:
        summary[split]={}
        for mode in ['keyword','vector','fusion','hybrid']:
            group=[r for r in results if r['split']==split and r['mode']==mode]
            summary[split][mode]={'recall_at_8':statistics.mean(r['recall_at_8'] for r in group),'ndcg_at_8':statistics.mean(r['ndcg_at_8'] for r in group),
                'mean_latency_ms':statistics.mean(r['retrieval']['latency_ms'] for r in group)}
    manifest.update(status='completed',metrics=summary)
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    db.engine.dispose()


if __name__=='__main__':main()
