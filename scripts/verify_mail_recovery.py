"""Synthetic paired backup/restore and real local index-switch verification."""
import os
import sys
import json
import sqlite3
import shutil
from pathlib import Path
from datetime import datetime
from email.message import EmailMessage
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
folder=ROOT/'artifacts'/'mail-recovery'/datetime.now().strftime('%Y%m%d-%H%M%S');folder.mkdir(parents=True)
os.environ.update(ZHIXING_MODE='demo',ZHIXING_MAIL_ADDRESS='',ZHIXING_MAIL_WORKER='1',ZHIXING_DATA_DIR=str(folder/'source'))
os.environ.pop('ZHIXING_DISABLE_LOCAL_MODELS',None)
from backend.app.persistence.store import Store
from backend.app.modules.mail.repository import initialize,save_account,migrate
from backend.app.modules.mail.schemas import MailAccount,DraftInput
from backend.app.modules.mail.ingestion import store_message
from backend.app.modules.mail.retrieval import rebuild_account,search
from backend.app.modules.mail.assistant import draft,submit_draft
from backend.app.agent.graph import work_once,approve
from backend.app.modules.mail.filtering import DEFAULT_RULES
db=Store(folder/'source'/'zhixing.db');initialize(db)
aid=save_account(db,MailAccount(name='恢复演练',address='synthetic@qq.com',enabled=True))['id']
db.insert('setting',{**DEFAULT_RULES,'whitelist_senders':['author@example.com']},id='mail-filter:'+aid)
msg=EmailMessage();msg['From']='author@example.com';msg['Subject']='恢复实验';msg.set_content('实验结论是冷却后重新测量。')
mid=store_message(db,aid,'recovery',1,msg.as_bytes(),'2026-09-22T10:00:00+00:00')
d=draft(db,DraftInput(account_id=aid,to=['author@example.com'],subject='实验回复',content='收到'))
rid=submit_draft(db,d['id'],1)['run_id'];work_once(db)
assert db.get(rid)['status']=='waiting_approval'
index=rebuild_account(db,aid)
from backend.app.modules.mail.retrieval import _close_vector_store
_close_vector_store(db)
backup=folder/'backup';backup.mkdir()
for source in db.path.parent.glob('*.db'):
        with sqlite3.connect(source) as original,sqlite3.connect(backup/source.name) as target:original.backup(target)
shutil.copytree(db.path.parent/'qdrant',backup/'qdrant')
migrate(db);assert db.get(rid)['status']=='migration_review'
restored_folder=folder/'restored';restored_folder.mkdir()
for source in backup.glob('*.db'):shutil.copy2(source,restored_folder/source.name)
shutil.copytree(backup/'qdrant',restored_folder/'qdrant')
restored=Store(restored_folder/'zhixing.db');initialize(restored)
assert restored.get(rid)['status']=='waiting_approval'
a=restored.for_run('approval',rid)[0];approve(a['id'],{'decision':'approve'},restored);work_once(restored)
assert restored.get(d['id'])['status']=='simulated'
result=search(restored,{'account_ids':[aid],'query':'实验应该怎么重新测量'})
assert index['recoverable_vector_switch'] and not result['degraded'] and result['evidence'][0]['message_id']==mid
report={'paired_restore':True,'approval_resume':True,'external_send':'simulated_no_network','real_local_rag':True,'index':index,'query':result}
(folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(folder,flush=True);print('paired restore, approval resume, real RAG index switch: passed',flush=True)
_close_vector_store(restored);_close_vector_store(db)
restored.engine.dispose();db.engine.dispose()
