"""Local pre-upgrade backup, including uncommitted source and private runtime data."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import shutil
import sqlite3
import subprocess
import zipfile

root = Path(__file__).resolve().parents[1]
target = root / 'backups' / ('mail-upgrade-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
target.mkdir(parents=True)
if (root / 'data/processes.json').exists():
    raise SystemExit('Stop project services before creating a consistent backup')
names = subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=root).decode().split('\0')
with zipfile.ZipFile(target/'source.zip','w',zipfile.ZIP_DEFLATED) as z:
    for name in dict.fromkeys(names):
        p=root/name
        if name and p.is_file():
            z.write(p,name)
for name in ['data','docs','logs']:
    source=root/name
    if source.exists():
        shutil.copytree(source,target/name,ignore=shutil.ignore_patterns('*.db','*.db-wal','*.db-shm','models','index-builds'))
model_manifest=root/'data/models/manifest.json'
if model_manifest.exists():shutil.copy2(model_manifest,target/'data/model-manifest.json')
for source in (root/'data').glob('*.db'):
    with sqlite3.connect(source) as src,sqlite3.connect(target/'data'/source.name) as dst:
        src.backup(dst)
for source in root.glob('.env*'):
    if source.is_file():
        shutil.copy2(source,target/source.name)
(target/'changes.patch').write_bytes(subprocess.check_output(['git','diff','HEAD','--binary'],cwd=root))
manifest={'at':datetime.now(timezone.utc).isoformat(),'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root).decode().strip(),
          'private':True,'files':{str(p.relative_to(target)):hashlib.sha256(p.read_bytes()).hexdigest() for p in target.rglob('*') if p.is_file()}}
(target/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
print(target)
