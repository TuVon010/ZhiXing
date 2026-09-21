import sqlite3
from datetime import datetime
from pathlib import Path
root=Path(__file__).resolve().parents[1]
target=root/'backups'/datetime.now().strftime('%Y%m%d-%H%M%S')
target.mkdir(parents=True)
for name in ['zhixing.db','checkpoints.db']:
    source=root/'data'/name
    if source.exists():
        with sqlite3.connect(source) as src,sqlite3.connect(target/name) as dst:
            src.backup(dst)
print(target)
