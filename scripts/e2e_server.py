import os
import sys
import threading
import time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
os.environ['ZHIXING_MODE']='demo'
os.environ['ZHIXING_DATA_DIR']=str(root/'.tmp'/('e2e-'+str(time.time_ns())))
from backend.runtime import work_once
from backend.main import app
from backend.evolution import seed
from backend.db import store
import uvicorn
seed(store)
def loop():
    while True:
        if not work_once():
            time.sleep(.1)
threading.Thread(target=loop,daemon=True).start()
uvicorn.run(app,host='127.0.0.1',port=8000)
