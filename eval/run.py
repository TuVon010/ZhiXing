import argparse
import json
import statistics
import sys
import time
import os
import hashlib
import shutil
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
parser=argparse.ArgumentParser()
parser.add_argument('--live',action='store_true')
parser.add_argument('--output-dir',type=Path)
parser.add_argument('--dataset',type=Path,default=Path(__file__).parent/'datasets'/'regression.jsonl')
args=parser.parse_args()
output=args.output_dir or Path(__file__).resolve().parents[1]/'artifacts'/'evaluations'/str(time.time_ns())
output.mkdir(parents=True,exist_ok=False)
os.environ['ZHIXING_DATA_DIR']=str(output.resolve()/'database')
os.environ['ZHIXING_MODE']='live' if args.live else 'demo'
from backend.app.agent.model_client import demo_plan, plan
from backend.app.agent.policy import risk, decide
from backend.app.persistence.store import store
from backend.app.core.config import settings
root=Path(__file__).resolve().parent
shutil.copy2(args.dataset,output/'dataset.jsonl')
rows=[json.loads(line) for line in args.dataset.read_text(encoding='utf-8').splitlines() if line.strip()]
if not rows:
    raise ValueError('Dataset is empty')
results=[];latencies=[]
prior_calls={r['id'] for r in store.list('model_call',limit=100000)}
if args.live:
    settings.mode='live'
for row in rows:
    start=time.perf_counter()
    try:
        p=plan(store,row['message'],{'skills':[],'parsers':[]}) if args.live else demo_plan(row['message'])
        tools=[a.tool for a in p.actions];risks=[risk(a.model_dump()) for a in p.actions]
        results.append({'id':row['id'],'split':row['split'],'tools_pass':tools==row['expected_tools'],'risk_pass':risks==row['expected_risks'],'clarification_pass':any(bool(a.clarification) for a in p.actions)==row['expected_clarification'],'high_risk_gated':all(decide(store,a.model_dump()) in {'ASK','CLARIFY'} for a in p.actions if risk(a.model_dump())=='HIGH')})
        fields=row.get('expected_fields',[])
        results[-1]['extraction_pass']=all(f['action']<len(p.actions) and p.actions[f['action']].args.get(f['field'])==f['value'] for f in fields) if fields else None
        results[-1]['actual_plan']=p.model_dump(mode='json')
        results[-1]['actual_risks']=risks
        results[-1]['actual_decisions']=[decide(store,a.model_dump()) for a in p.actions]
    except Exception as e:
        results.append({'id':row['id'],'split':row['split'],'error':type(e).__name__})
    latencies.append((time.perf_counter()-start)*1000)
    results[-1]['case']=row
    results[-1]['latency_ms']=latencies[-1]
    with (output/'cases.jsonl').open('a',encoding='utf-8') as evidence:
        evidence.write(json.dumps(results[-1],ensure_ascii=False)+'\n')
report={'mode':'live' if args.live else 'offline_rule_regression','dataset':'synthetic','cases':len(rows),'model_quality_verified':bool(args.live),'cost_per_message':None,'token_per_message':None,'parser_hit_rate':None,'p50_ms':statistics.median(latencies),'p95_ms':sorted(latencies)[int(len(latencies)*.95)-1],'splits':{}}
for split in ['train','holdout']:
    subset=[r for r in results if r['split']==split]
    report['splits'][split]={key:sum(bool(r.get(key)) for r in subset)/len(subset) if subset else None for key in ['tools_pass','risk_pass','clarification_pass','high_risk_gated']}
    labeled=[r for r in subset if r['case'].get('expected_fields')]
    report['splits'][split]['extraction_pass']=sum(bool(r.get('extraction_pass')) for r in labeled)/len(labeled) if labeled else None
calls=[r for r in store.list('model_call',limit=100000) if r['id'] not in prior_calls]
if calls:
    if all((r['body'].get('usage') or {}).get('total_tokens') is not None for r in calls):
        report['token_per_message']=sum(r['body']['usage']['total_tokens'] for r in calls)/len(rows)
    if all(r['body'].get('cost') is not None for r in calls):
        report['cost_per_message']=sum(r['body']['cost'] for r in calls)/len(rows)
report['failed_cases']=sum('error' in r or any(r.get(k) is False for k in ['tools_pass','risk_pass','clarification_pass','high_risk_gated','extraction_pass']) for r in results)
report['dataset_sha256']=hashlib.sha256(args.dataset.read_bytes()).hexdigest()
report['model_quality_verified']=False
report['verification']='real_model_api' if args.live else 'offline_synthetic_rules_only'
report['results']=results
(output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='results'},ensure_ascii=False,indent=2))
print('Evidence:',output.resolve())
sys.exit(1 if report['failed_cases'] else 0)
