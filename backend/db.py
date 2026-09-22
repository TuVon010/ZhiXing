import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import create_engine, event, text
from .config import settings

def now():
    return datetime.now(timezone.utc).isoformat()

def uid():
    return uuid.uuid4().hex

def encode(value):
    return json.dumps(value, ensure_ascii=False, default=str)

class Store:
    def __init__(self, path=None):
        self.path = Path(path or Path(settings.data_dir) / 'zhixing.db').resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine('sqlite:///' + self.path.as_posix(), connect_args={'timeout': 30})
        @event.listens_for(self.engine, 'connect')
        def configure(conn, _):
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA foreign_keys=ON')
        with self.engine.begin() as c:
            c.execute(text('CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)'))
            c.execute(text('CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, scope TEXT NOT NULL, dedupe TEXT UNIQUE, body TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)'))
            c.execute(text('CREATE INDEX IF NOT EXISTS records_kind ON records(kind,status,created_at)'))
            c.execute(text("CREATE INDEX IF NOT EXISTS records_run ON records(kind,json_extract(body,'$.run_id'),created_at)"))
            c.execute(text('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE, scope TEXT NOT NULL, status TEXT NOT NULL, lease_until REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0, resume TEXT, created_at TEXT NOT NULL)'))
            c.execute(text('CREATE TABLE IF NOT EXISTS ledger(action_id TEXT PRIMARY KEY, status TEXT NOT NULL, result TEXT, updated_at TEXT NOT NULL)'))
            c.execute(text('INSERT OR IGNORE INTO migrations VALUES(1,:at)'), {'at': now()})

    def insert(self, kind, body, status='active', scope='local', dedupe=None, id=None, conn=None):
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
        if conn is not None:
            row = conn.execute(text('SELECT * FROM records WHERE id=:id'), {'id': id}).mappings().first()
        else:
            with self.engine.connect() as c:
                row = c.execute(text('SELECT * FROM records WHERE id=:id'), {'id': id}).mappings().first()
        if not row:
            raise KeyError(id)
        return {**dict(row), 'body': json.loads(row['body'])}

    def update(self, id, body=None, status=None, conn=None):
        def apply(c):
            old = self.get(id, c)
            c.execute(text('UPDATE records SET body=:body,status=:status,updated_at=:at WHERE id=:id'), dict(id=id, body=encode(old['body'] if body is None else body), status=status or old['status'], at=now()))
        if conn is not None:
            apply(conn)
        else:
            with self.engine.begin() as c:
                apply(c)

    def list(self, kind, limit=100, offset=0, status=None, scope=None):
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
        node = {'MESSAGE_RECEIVED':'gateway','UNDERSTAND_STARTED':'understand','ACTION_PLANNED':'plan',
                'MODEL_REQUEST':'model','MODEL_RESPONSE':'model','MODEL_FAILED':'model',
                'JEV_REQUEST':'jev','JEV_RESPONSE':'jev','JEV_FAILED':'jev','JEV_SKIPPED':'jev',
                'RISK_CHECKED':'risk','APPROVAL_REQUESTED':'approval','APPROVAL_EDITED':'approval',
                'APPROVAL_DECIDED':'approval','TOOL_CALLED':'execute','TOOL_RESULT':'execute'}.get(event_type,'runtime')
        return self.insert('audit', dict(run_id=run_id, trace_id=run_id, event_type=event_type, node=node, **payload), scope=run_id)

    def for_run(self, kind, run_id):
        with self.engine.connect() as c:
            rows = c.execute(text("SELECT * FROM records WHERE kind=:kind AND json_extract(body,'$.run_id')=:run ORDER BY created_at,id"),{'kind':kind,'run':run_id}).mappings().all()
        return [{**dict(r),'body':json.loads(r['body'])} for r in rows]

store = Store()
