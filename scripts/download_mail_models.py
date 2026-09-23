"""Download immutable revisions to E:, retaining hashes for reproducibility."""
import os
import json
import hashlib
import time
import sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
os.environ['HF_HOME']=str(root.parent/'.cache'/'huggingface')
os.environ['HF_HUB_DISABLE_XET']='1'
from huggingface_hub import HfApi,snapshot_download,set_client_factory
import httpx
set_client_factory(lambda:httpx.Client(timeout=httpx.Timeout(180,connect=30),follow_redirects=True))
destination=root/'data'/'models';destination.mkdir(parents=True,exist_ok=True)
manifest={}
locked=json.loads((destination/'manifest.json').read_text(encoding='utf-8')) if (destination/'manifest.json').exists() else {}
for key,name in [('embedding','intfloat/multilingual-e5-small'),('reranker','cross-encoder/mmarco-mMiniLMv2-L12-H384-v1')]:
    for attempt in range(4):
        try:
            revision=locked.get(key,{}).get('revision') if '--update' not in sys.argv else None
            revision=revision or HfApi().model_info(name).sha
            path=snapshot_download(name,revision=revision,local_dir=destination/key,max_workers=1,etag_timeout=60,allow_patterns=['*.json','*.safetensors','*.model','vocab.txt','modules.json','1_Pooling/*'],ignore_patterns=['onnx/*','openvino/*'])
            break
        except Exception as exc:
            print(key,'download attempt',attempt+1,type(exc).__name__,flush=True)
            if attempt==3:raise
            time.sleep(3)
    hashes={str(p.relative_to(path)):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(path).rglob('*') if p.is_file() and '.cache' not in p.parts}
    manifest[key]={'name':name,'revision':revision,'path':str(Path(path).resolve()),'sha256':hashes}
    print(key,revision,flush=True)
(destination/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
