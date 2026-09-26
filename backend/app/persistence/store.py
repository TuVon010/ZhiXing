"""SQLite persistence boundary shared by API, Agent and workers.

The store owns connection configuration and transaction-aware record helpers.
Business modules decide what a record means; this module only guarantees
durable CRUD, deduplication and audit storage.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import create_engine, event, text
from backend.app.core.config import settings

def now():
    return datetime.now(timezone.utc).isoformat()

def uid():
    return uuid.uuid4().hex

def encode(value):
    return json.dumps(value, ensure_ascii=False, default=str)

class Store:
    """Small transaction-friendly repository for versioned local records."""

    def __init__(self, path=None):
        # 1. 确定数据库文件路径，并确保父目录存在
        self.path = Path(path or Path(settings.data_dir) / 'zhixing.db').resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # 2. 创建 SQLite 引擎，设置 30 秒超时防止并发写入报错
        self.engine = create_engine('sqlite:///' + self.path.as_posix(), connect_args={'timeout': 30})

        # 3. 每次建立新连接时，强制开启 WAL 模式和外键约束
        @event.listens_for(self.engine, 'connect')
        def configure(conn, _):
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA foreign_keys=ON')

        # 4. 开启事务，执行建表与初始化操作
        with self.engine.begin() as c:

            # migrations 表：记录数据库版本，用于未来的表结构升级
            c.execute(text(
                'CREATE TABLE IF NOT EXISTS migrations('
                'version INTEGER PRIMARY KEY, '
                'applied_at TEXT NOT NULL'
                ')'
            ))

            # records 表：核心业务大表，所有数据（邮件/待办/日程等）通过 kind 区分
            c.execute(text(
                'CREATE TABLE IF NOT EXISTS records('
                'id TEXT PRIMARY KEY, '
                'kind TEXT NOT NULL, '
                'status TEXT NOT NULL, '
                'scope TEXT NOT NULL, '
                'dedupe TEXT UNIQUE, '  # 去重键，防止同一条数据重复入库
                'body TEXT NOT NULL, '   # JSON 字符串，存储业务数据本体
                'created_at TEXT NOT NULL, '
                'updated_at TEXT NOT NULL'
                ')'
            ))

            # 索引：加速“按类型+状态过滤，按时间排序”的查询
            c.execute(text(
                'CREATE INDEX IF NOT EXISTS records_kind '
                'ON records(kind, status, created_at)'
            ))

            # 索引：从 body 的 JSON 中提取 run_id 建索引，加速同批次记录查询
            c.execute(text(
                "CREATE INDEX IF NOT EXISTS records_run "
                "ON records(kind, json_extract(body, '$.run_id'), created_at)"
            ))

            # jobs 表：异步任务队列，供后台 Worker 领取执行
            c.execute(text(
                'CREATE TABLE IF NOT EXISTS jobs('
                'id TEXT PRIMARY KEY, '
                'run_id TEXT NOT NULL UNIQUE, '
                'scope TEXT NOT NULL, '
                'status TEXT NOT NULL, '
                'lease_until REAL NOT NULL DEFAULT 0, '  # 租约时间，防止任务被并发执行
                'attempts INTEGER NOT NULL DEFAULT 0, '  # 失败重试次数
                'resume TEXT, '                          # 断点续跑信息
                'created_at TEXT NOT NULL'
                ')'
            ))

            # ledger 表：操作流水账，保证发邮件等外部操作的幂等性
            c.execute(text(
                'CREATE TABLE IF NOT EXISTS ledger('
                'action_id TEXT PRIMARY KEY, '
                'status TEXT NOT NULL, '
                'result TEXT, '
                'updated_at TEXT NOT NULL'
                ')'
            ))

            # 写入初始版本号。INSERT OR IGNORE 保证重复启动时不会报错
            c.execute(
                text('INSERT OR IGNORE INTO migrations (version, applied_at) VALUES(1, :at)'),
                {'at': now()}
            )

    def insert(self, kind, body, status='active', scope='local', dedupe=None, id=None, conn=None):
        """Insert one record, optionally participating in the caller's transaction."""
        ident = id or uid()
        args = dict(id=ident, kind=kind, status=status, scope=scope, dedupe=dedupe, body=encode(body), at=now())
        sql = text('INSERT INTO records VALUES(:id,:kind,:status,:scope,:dedupe,:body,:at,:at)')
        if conn is not None:
            conn.execute(sql, args)
        else:
            with self.engine.begin() as c:
                c.execute(sql, args)
        return ident

    def get(self, id, conn=None):
        """Load and decode one record or raise ``KeyError`` when it is absent."""
        if conn is not None:
            row = conn.execute(text('SELECT * FROM records WHERE id=:id'), {'id': id}).mappings().first()
        else:
            with self.engine.connect() as c:
                row = c.execute(text('SELECT * FROM records WHERE id=:id'), {'id': id}).mappings().first()
        if not row:
            raise KeyError(id)
        return {**dict(row), 'body': json.loads(row['body'])}

    def update(self, id, body=None, status=None, conn=None):
        """Replace record state atomically while preserving unspecified fields."""
        def apply(c):
            old = self.get(id, c)
            c.execute(text('UPDATE records SET body=:body,status=:status,updated_at=:at WHERE id=:id'), dict(id=id, body=encode(old['body'] if body is None else body), status=status or old['status'], at=now()))
        if conn is not None:
            apply(conn)
        else:
            with self.engine.begin() as c:
                apply(c)

    def list(self, kind, limit=100, offset=0, status=None, scope=None):
        """Return newest records for a kind with optional status and scope filters."""
        sql = 'SELECT * FROM records WHERE kind=:kind'
        args = dict(kind=kind, limit=limit, offset=offset)
        for key, value in [('status', status), ('scope', scope)]:
            if value is not None:
                sql += f' AND {key}=:{key}'
                args[key] = value
        with self.engine.connect() as c:
            rows = c.execute(text(sql + ' ORDER BY created_at DESC LIMIT :limit OFFSET :offset'), args).mappings().all()
        return [{**dict(r), 'body': json.loads(r['body'])} for r in rows]

    def audit(self, run_id, event_type, **payload):
        """Append a durable, user-visible event to a run's execution trace."""
        #  事件类型 -> 执行节点映射，未匹配的默认归为 runtime
        node = {'MESSAGE_RECEIVED':'gateway','UNDERSTAND_STARTED':'understand','ACTION_PLANNED':'plan',
                'MODEL_REQUEST':'model','MODEL_RESPONSE':'model','MODEL_FAILED':'model',
                'RISK_CHECKED':'risk','APPROVAL_REQUESTED':'approval','APPROVAL_EDITED':'approval',
                'APPROVAL_DECIDED':'approval','TOOL_CALLED':'execute','TOOL_RESULT':'execute'}.get(event_type,'runtime')
        # 写入 audit 记录，scope 绑定 run_id
        return self.insert('audit', dict(run_id=run_id, trace_id=run_id, event_type=event_type, node=node, **payload), scope=run_id)

    def for_run(self, kind, run_id):
        """Return records linked to a run through the indexed JSON run identifier."""
        with self.engine.connect() as c:
            rows = c.execute(text("SELECT * FROM records WHERE kind=:kind AND json_extract(body,'$.run_id')=:run ORDER BY created_at,id"),{'kind':kind,'run':run_id}).mappings().all()
        return [{**dict(r),'body':json.loads(r['body'])} for r in rows]

store = Store()
