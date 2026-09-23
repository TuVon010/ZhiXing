import os
import sys
import threading
import time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
os.environ['ZHIXING_MODE']='demo'
os.environ['ZHIXING_MAIL_WORKER']='1'
os.environ['ZHIXING_DISABLE_LOCAL_MODELS']='1'
os.environ['ZHIXING_DISABLE_MAIL_NETWORK']='1'
os.environ['ZHIXING_DATA_DIR']=os.environ.get('ZHIXING_E2E_DATA_DIR',str(root/'.tmp'/('e2e-'+str(time.time_ns()))))
port=int(os.environ.get('ZHIXING_E2E_PORT','8000'))
os.environ['ZHIXING_E2E_ORIGIN']=f'http://127.0.0.1:{port}'
from backend.runtime import work_once
from backend.mail_worker import work_once as mail_work_once
from backend.main import app
from backend.evolution import seed
from backend.db import store
import uvicorn
seed(store)
def loop():
    while True:
        if not mail_work_once(store) and not work_once():
            time.sleep(.1)
threading.Thread(target=loop,daemon=True).start()
uvicorn.run(app,host='127.0.0.1',port=port)
