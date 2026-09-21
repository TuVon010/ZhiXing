import argparse
import json
import statistics
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.planner import demo_plan, plan
from backend.policy import risk, decide
from backend.db import store
from backend.config import settings
parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');args=parser.parse_args()
root=Path(__file__).resolve().parent
rows=[json.loads(line) for line in (root/'datasets'/'regression.jsonl').read_text(encoding='utf-8').splitlines()]
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
    except Exception as e:
        results.append({'id':row['id'],'split':row['split'],'error':type(e).__name__})
    latencies.append((time.perf_counter()-start)*1000)
report={'mode':'live' if args.live else 'offline_rule_regression','dataset':'synthetic','cases':len(rows),'model_quality_verified':bool(args.live),'cost_per_message':None,'token_per_message':None,'parser_hit_rate':None,'p50_ms':statistics.median(latencies),'p95_ms':sorted(latencies)[int(len(latencies)*.95)-1],'splits':{}}
for split in ['train','holdout']:
    subset=[r for r in results if r['split']==split]
    report['splits'][split]={key:sum(bool(r.get(key)) for r in subset)/len(subset) for key in ['tools_pass','risk_pass','clarification_pass','high_risk_gated']}
    labeled=[r for r in subset if r.get('extraction_pass') is not None]
    report['splits'][split]['extraction_pass']=sum(r['extraction_pass'] for r in labeled)/len(labeled) if labeled else None
calls=[r for r in store.list('model_call',limit=100000) if r['id'] not in prior_calls]
if calls:
    report['token_per_message']=sum(r['body'].get('usage',{}).get('total_tokens',0) for r in calls)/len(rows)
    if all(r['body'].get('cost') is not None for r in calls):
        report['cost_per_message']=sum(r['body']['cost'] for r in calls)/len(rows)
report['results']=results
(root/'reports').mkdir(exist_ok=True)
(root/'reports'/('live.json' if args.live else 'offline.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='results'},ensure_ascii=False,indent=2))
