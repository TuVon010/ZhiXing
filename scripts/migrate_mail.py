"""Stopped-service upgrade. Keeps an immutable pre-migration private backup."""
import sys
import subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
if (ROOT/'data/processes.json').exists():raise SystemExit('请先运行 scripts/stop.ps1')
from backend.app.persistence.store import store
from backend.app.modules.mail.repository import migrate
from sqlalchemy import text
with store.engine.connect() as c:
    exists=c.execute(text('SELECT 1 FROM migrations WHERE version=2')).first()
if exists:
    print('邮件迁移已完成，无需重复执行')
else:
    subprocess.run([sys.executable,str(ROOT/'scripts/snapshot_mail_upgrade.py')],cwd=ROOT,check=True)
    migrate(store)
    print('迁移完成。旧运行等待人工复核；旧邮箱默认暂停。')
