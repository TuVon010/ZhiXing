"""One-time local database rename; run only with API and Worker stopped."""
import sqlite3
from pathlib import Path

def migrate(data:Path):
    if (data/'processes.json').exists():
        raise RuntimeError('请先运行 stop.ps1 停止服务，再迁移数据库名称')
    previous=data/'pulse.db'
    target=data/'zhixing.db'
    if target.exists() or not previous.exists():
        return False
    with sqlite3.connect(previous) as source, sqlite3.connect(target) as destination:
        source.backup(destination)
        if destination.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise RuntimeError('数据库迁移完整性检查失败')
    return True

if __name__=='__main__':
    folder=Path(__file__).resolve().parents[1]/'data'
    print('已迁移为 zhixing.db，原数据库保留为迁移备份' if migrate(folder) else '无需迁移数据库名称')
