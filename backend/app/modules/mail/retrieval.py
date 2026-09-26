"""Local hybrid retrieval with immutable model revisions and source citations."""
import hashlib
import json
import os
import atexit
from pathlib import Path
import re
import threading
import time
from datetime import timezone
from sqlalchemy import text,bindparam
from backend.app.modules.mail.repository import initialize,require,validate_accounts
from backend.app.core.config import ROOT

_lock=threading.RLock()
_models=None
_loaded_revision=None
_vector_clients={}
_VECTOR_COLLECTION='mail_vectors'


def _vector_client(db):
    """Open one persistent Qdrant local store per business database.

    The local store is process-locked by Qdrant, so live access stays in the
    serialized mail Worker; API requests enqueue searches instead.
    """
    from qdrant_client import QdrantClient
    path=(db.path.parent/'qdrant').resolve()
    key=str(path)
    with _lock:
        client=_vector_clients.get(key)
        if client is None:
            path.mkdir(parents=True,exist_ok=True)
            client=QdrantClient(path=str(path))
            _vector_clients[key]=client
        return client


def _upsert_vectors(db, rows):
    rows=[r for r in rows if r.get('embedding') is not None]
    if not rows:return
    from qdrant_client import models
    from uuid import uuid5,NAMESPACE_URL
    import numpy as np
    client=_vector_client(db);dimension=len(rows[0]['embedding'])//4
    if not client.collection_exists(_VECTOR_COLLECTION):
        client.create_collection(_VECTOR_COLLECTION, vectors_config=models.VectorParams(size=dimension,distance=models.Distance.COSINE))
    points=[]
    for row in rows:
        points.append(models.PointStruct(id=str(uuid5(NAMESPACE_URL,'zhixing-mail-chunk:'+row['id']+':'+row['model_version'])),vector=np.frombuffer(row['embedding'],dtype=np.float32).tolist(),payload={
            'chunk_id':row['id'],
            'account_id':row['account_id'],'message_id':row['message_id'],'thread_id':row['thread_id'],
            'model_version':row['model_version'],'received_at':row['received_at'],'sender':row['sender'],
            'location':row['location'],'content_hash':row['content_hash']}))
    client.upsert(collection_name=_VECTOR_COLLECTION,points=points,wait=True)


def _delete_vectors(db,account_id,message_id=None):
    from qdrant_client import models
    client=_vector_client(db)
    if not client.collection_exists(_VECTOR_COLLECTION):return
    must=[models.FieldCondition(key='account_id',match=models.MatchValue(value=account_id))]
    if message_id:must.append(models.FieldCondition(key='message_id',match=models.MatchValue(value=message_id)))
    client.delete(collection_name=_VECTOR_COLLECTION,points_selector=models.FilterSelector(filter=models.Filter(must=must)),wait=True)


def _close_vector_store(db):
    key=str((db.path.parent/'qdrant').resolve())
    with _lock:
        client=_vector_clients.pop(key,None)
        if client is not None:client.close()


def _close_all_vector_stores():
    with _lock:
        clients=list(_vector_clients.values());_vector_clients.clear()
    for client in clients:
        try:client.close()
        except Exception:pass


atexit.register(_close_all_vector_stores)


def _ensure_vector_index(db,accounts,version):
    """Backfill the Qdrant index from the retained SQLite vector cache."""
    from qdrant_client import models
    client=_vector_client(db)
    if not client.collection_exists(_VECTOR_COLLECTION):
        with db.engine.connect() as c:
            rows=c.execute(text("SELECT * FROM mail_chunks WHERE account_id IN :accounts AND model_version=:v AND embedding IS NOT NULL" ).bindparams(bindparam('accounts',expanding=True)),{'accounts':accounts,'v':version}).mappings().all()
        _upsert_vectors(db,[dict(r) for r in rows]);return
    for account in accounts:
        filt=models.Filter(must=[models.FieldCondition(key='account_id',match=models.MatchValue(value=account)),models.FieldCondition(key='model_version',match=models.MatchValue(value=version))])
        stored=client.count(_VECTOR_COLLECTION,count_filter=filt,exact=True).count
        with db.engine.connect() as c:
            rows=c.execute(text("SELECT * FROM mail_chunks WHERE account_id=:a AND model_version=:v AND embedding IS NOT NULL"),{'a':account,'v':version}).mappings().all()
        if stored==len(rows):continue
        client.delete(_VECTOR_COLLECTION,points_selector=models.FilterSelector(filter=filt),wait=True)
        _upsert_vectors(db,[dict(r) for r in rows])


def model_manifest():
    path=ROOT/'data/models/manifest.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def model_version():
    m=model_manifest()
    return m.get('embedding',{}).get('revision','keyword-only')


def load_models():
    global _models,_loaded_revision
    if os.environ.get('ZHIXING_MAIL_WORKER')!='1' or os.environ.get('ZHIXING_DISABLE_LOCAL_MODELS')=='1':
        raise ValueError('本地模型仅由 Worker 加载')
    with _lock:
        manifest=model_manifest()
        revision=tuple(manifest.get(k,{}).get('revision') for k in ('embedding','reranker'))
        if _models is None or revision!=_loaded_revision:
            if not {'embedding','reranker'}<=manifest.keys():raise ValueError('本地模型尚未安装')
            for spec in manifest.values():
                for relative,expected in spec.get('sha256',{}).items():
                    path=Path(spec['path'])/relative
                    with path.open('rb') as source:
                        actual=hashlib.file_digest(source,'sha256').hexdigest()
                    if actual!=expected:raise ValueError('模型文件哈希不匹配，请重新安装')
            os.environ.setdefault('HF_HOME',str(ROOT.parent/'.cache/huggingface'))
            os.environ.setdefault('TORCH_HOME',str(ROOT.parent/'.cache/torch'))
            from sentence_transformers import SentenceTransformer,CrossEncoder
            import torch
            torch.set_num_threads(2)
            _models=(SentenceTransformer(manifest['embedding']['path'],device='cpu',local_files_only=True),
                     CrossEncoder(manifest['reranker']['path'],device='cpu',local_files_only=True))
            _loaded_revision=revision
        return _models


def tokens(value):
    import jieba
    jieba.setLogLevel(40);jieba.dt.tmp_dir=str(ROOT/'.tmp')
    return ' '.join(w.lower() for w in jieba.cut(value) if w.strip() and re.search(r'\w',w))


def chunks(value,model=None):
    if model is None:
        # Explicit keyword fallback; not claimed to be model-token chunks.
        return [value[i:i+640] for i in range(0,len(value),544)]
    tokenizer=model.tokenizer;ids=tokenizer.encode(value,add_special_tokens=False)
    return [tokenizer.decode(ids[i:i+320],skip_special_tokens=True) for i in range(0,len(ids),272)]


def index_message(db,ident,attachment_root=None):
    initialize(db);row=require(db,ident,'mail_message');b=row['body']
    if row['status'] not in {'active','archived','legacy'}:return {'skipped':True}
    started=time.monotonic();mode='hybrid';reason=None
    try:embedding,_=load_models()
    except Exception as exc:embedding=None;mode='keyword';reason=type(exc).__name__
    if embedding:
        with db.engine.connect() as c:
            previous=c.execute(text("SELECT DISTINCT model_version FROM mail_chunks WHERE account_id=:aid AND model_version!='keyword-only'"),{'aid':row['scope']}).scalars().all()
        if any(v!=model_version() for v in previous):
            raise ValueError('嵌入版本已改变，请重建整个账号索引后切换')
    segments=[{'location':'正文','text':b.get('subject','')+'\n'+b.get('text','')}]
    attachments=list(b.get('attachments',[]))
    from backend.app.modules.mail.attachments import extract
    for item in attachments:
        if item['status']=='pending':
            result=extract((attachment_root or db.path.parent/'mail/attachments')/item['id'],item['format'])
            item.update(result)
        for segment in item.get('segments',[]):
            segments.append({'location':item['id']+' / '+segment['location'],'text':segment['text']})
    all_chunks=[]
    for segment in segments:
        for i,content in enumerate(chunks(segment['text'],embedding)):
            if content.strip():all_chunks.append((segment['location']+f' / 块 {i+1}',content))
    version=model_version() if embedding else 'keyword-only';vectors={}
    if embedding:
        for _,content in all_chunks:
            digest=hashlib.sha256(content.encode()).hexdigest()
            with db.engine.connect() as c:
                hit=c.execute(text('SELECT embedding FROM mail_vectors WHERE content_hash=:h AND model_version=:v'),{'h':digest,'v':version}).first()
            if hit:vectors[digest]=hit[0];continue
            with _lock:vector=embedding.encode(['passage: '+content],normalize_embeddings=True)[0].astype('float32').tobytes()
            vectors[digest]=vector
            with db.engine.begin() as c:c.execute(text('INSERT OR IGNORE INTO mail_vectors VALUES(:h,:v,:e)'),{'h':digest,'v':version,'e':vector})
    # Publish the complete message index atomically. Readers never see half an index.
    with db.engine.begin() as c:
        c.execute(text('DELETE FROM mail_fts WHERE chunk_id IN (SELECT id FROM mail_chunks WHERE message_id=:id)'),{'id':ident})
        c.execute(text('DELETE FROM mail_chunks WHERE message_id=:id'),{'id':ident})
        for i,(location,content) in enumerate(all_chunks):
            cid=ident+'-chunk-'+str(i);digest=hashlib.sha256(content.encode()).hexdigest()
            c.execute(text('INSERT INTO mail_chunks VALUES(:id,:a,:m,:t,:loc,:content,:hash,:embedding,:version,:received,:sender)'),
                {'id':cid,'a':row['scope'],'m':ident,'t':b['thread_id'],'loc':location,'content':content,'hash':digest,
                 'embedding':vectors.get(digest),'version':version,'received':b['received_at'],'sender':b.get('sender','')})
            c.execute(text('INSERT INTO mail_fts VALUES(:id,:content)'),{'id':cid,'content':tokens(content)})
        current=db.get(ident,c)
        db.update(ident,{**current['body'],'attachments':attachments,'index_status':'indexed' if embedding else 'keyword_only','index_version':version},conn=c)
    _delete_vectors(db,row['scope'],ident)
    if embedding:
        vector_rows=[]
        for i,(location,content) in enumerate(all_chunks):
            cid=ident+'-chunk-'+str(i);digest=hashlib.sha256(content.encode()).hexdigest()
            vector_rows.append({'id':cid,'account_id':row['scope'],'message_id':ident,'thread_id':b['thread_id'],'location':location,
                'content_hash':digest,'embedding':vectors[digest],'model_version':version,'received_at':b['received_at'],'sender':b.get('sender','').lower()})
        _upsert_vectors(db,vector_rows)
    return {'mode':mode,'degraded':not bool(embedding),'reason':reason,'chunks':len(all_chunks),'model_version':version,'latency_ms':round((time.monotonic()-started)*1000)}


def rebuild_account(db,account_id):
    """Build a separate database, then switch the whole account in one commit."""
    from backend.app.persistence.store import Store,uid
    from backend.app.modules.mail.repository import rows
    require(db,account_id,'mail_account');load_models();version=model_version()
    messages=[r for r in rows(db,'mail_message',[account_id],limit=100000) if r['status'] in {'active','archived','legacy'}]
    snapshot={r['id']:r['updated_at'] for r in messages}
    folder=db.path.parent/'index-builds'/uid();folder.mkdir(parents=True)
    stage=Store(folder/'candidate.db');initialize(stage)
    try:
        for row in messages:
            stage.insert('mail_message',row['body'],id=row['id'],scope=row['scope'],status=row['status'])
            result=index_message(stage,row['id'],db.path.parent/'mail/attachments')
            if result['degraded'] or result['model_version']!=version:raise ValueError('候选索引未完整建立，保留原索引')
        with stage.engine.connect() as source:
            candidate=source.execute(text('SELECT * FROM mail_chunks')).mappings().all()
        with db.engine.connect() as c:
            c.exec_driver_sql('BEGIN IMMEDIATE')
            current=c.execute(text("SELECT id,updated_at FROM records WHERE kind='mail_message' AND scope=:a AND status IN ('active','archived','legacy')"),{'a':account_id}).all()
            if dict(current)!=snapshot or version!=model_version():raise ValueError('构建期间资料或模型发生变化，请重试；原索引未改变')
            c.execute(text('DELETE FROM mail_fts WHERE chunk_id IN (SELECT id FROM mail_chunks WHERE account_id=:a)'),{'a':account_id})
            c.execute(text('DELETE FROM mail_chunks WHERE account_id=:a'),{'a':account_id})
            for row in candidate:
                c.execute(text('INSERT INTO mail_chunks VALUES(:id,:account_id,:message_id,:thread_id,:location,:content,:content_hash,:embedding,:model_version,:received_at,:sender)'),dict(row))
                c.execute(text('INSERT INTO mail_fts VALUES(:id,:content)'),{'id':row['id'],'content':tokens(row['content'])})
            for row in messages:
                body=stage.get(row['id'])['body'];db.update(row['id'],body,conn=c)
            c.commit()
        # Qdrant points carry the same account-scoped metadata as mail_chunks.
        # The SQL index is committed first; if this step fails it can be rebuilt.
        _delete_vectors(db,account_id)
        _upsert_vectors(db,[dict(r) for r in candidate])
        return {'account_id':account_id,'model_version':version,'messages':len(messages),'chunks':len(candidate),'candidate_database':str(stage.path),'atomic_switch':False,'recoverable_vector_switch':True}
    finally:
        stage.engine.dispose();_close_vector_store(stage)


def search(db,request,frozen_version=None):
    from backend.app.modules.mail.schemas import SearchRequest
    import numpy as np
    initialize(db);q=SearchRequest.model_validate(request);accounts=validate_accounts(db,q.account_ids);started=time.monotonic()
    conditions=["c.account_id IN :accounts","r.status IN ('active','archived','legacy')"]
    args={'accounts':accounts}
    if q.start:conditions.append('c.received_at>=:start');args['start']=q.start.astimezone(timezone.utc).isoformat()
    if q.end:conditions.append('c.received_at<:end');args['end']=q.end.astimezone(timezone.utc).isoformat()
    if q.sender:conditions.append('c.sender=:sender');args['sender']=q.sender.lower()
    clause=' AND '.join(conditions)
    keyword=[];dense=[];lookup={};degraded=False;reason=None
    words=list(dict.fromkeys(tokens(q.query).split()))[:40]
    if words:
        match=' OR '.join('"'+w.replace('"','""')+'"' for w in words)
        sql=text('SELECT c.*,bm25(mail_fts) AS rank FROM mail_fts JOIN mail_chunks c ON c.id=mail_fts.chunk_id JOIN records r ON r.id=c.message_id WHERE mail_fts MATCH :match AND '+clause+' ORDER BY rank,c.id LIMIT 40').bindparams(bindparam('accounts',expanding=True))
        with db.engine.connect() as conn:
            found=conn.execute(sql,{**args,'match':match}).mappings().all()
        for row in found:lookup[row['id']]=dict(row);keyword.append(row['id'])
    current=model_version();version=frozen_version or current
    if q.mode!='keyword':
        try:
            if version!=current:raise ValueError('运行模型版本已改变，使用关键词回退')
            embedding,reranker=load_models()
            with _lock:query=embedding.encode(['query: '+q.query],normalize_embeddings=True)[0]
            _ensure_vector_index(db,accounts,version)
            from qdrant_client import models
            must=[models.FieldCondition(key='account_id',match=models.MatchAny(any=accounts)),
                  models.FieldCondition(key='model_version',match=models.MatchValue(value=version))]
            if q.start:must.append(models.FieldCondition(key='received_at',range=models.DatetimeRange(gte=q.start.astimezone(timezone.utc))))
            if q.end:must.append(models.FieldCondition(key='received_at',range=models.DatetimeRange(lt=q.end.astimezone(timezone.utc))))
            if q.sender:must.append(models.FieldCondition(key='sender',match=models.MatchValue(value=q.sender.lower())))
            client=_vector_client(db)
            hits=(client.query_points(collection_name=_VECTOR_COLLECTION,query=query.tolist(),query_filter=models.Filter(must=must),limit=40,with_payload=True).points
                  if client.collection_exists(_VECTOR_COLLECTION) else [])
            ids=[hit.payload['chunk_id'] for hit in hits if hit.payload and hit.payload.get('chunk_id')]
            if ids:
                sql=text('SELECT c.* FROM mail_chunks c JOIN records r ON r.id=c.message_id WHERE c.id IN :ids AND '+clause+" AND r.status IN ('active','archived','legacy') AND c.model_version=:version").bindparams(bindparam('accounts',expanding=True),bindparam('ids',expanding=True))
                with db.engine.connect() as conn:found=conn.execute(sql,{**args,'version':version,'ids':ids}).mappings().all()
                by_id={r['id']:dict(r) for r in found}
                for hit in hits:
                    cid=(hit.payload or {}).get('chunk_id')
                    if cid in by_id:lookup[cid]=by_id[cid];dense.append(cid)
            # Missing vectors remain visible as a degraded index, not false hybrid success.
            check=text('SELECT COUNT(*) FROM mail_chunks c JOIN records r ON r.id=c.message_id WHERE '+clause+' AND (c.embedding IS NULL OR c.model_version!=:version)').bindparams(bindparam('accounts',expanding=True))
            with db.engine.connect() as conn:missing=conn.execute(check,{**args,'version':version}).scalar_one()
            if missing:degraded=True;reason='部分资料尚无当前版本向量'
        except Exception as exc:
            degraded=True;reason=type(exc).__name__;dense=[]
    if q.mode=='keyword':ordered=keyword[:20]
    elif q.mode=='vector' and not degraded:ordered=dense[:20]
    else:
        scores={}
        for result in (keyword,dense):
            for i,cid in enumerate(result):scores[cid]=scores.get(cid,0)+1/(60+i+1)
        ordered=sorted(scores,key=lambda k:(-scores[k],k))[:20]
    fusion=list(ordered)
    if q.mode=='hybrid' and dense and ordered:
        try:
            with _lock:rank=reranker.predict([(q.query,lookup[k]['content']) for k in ordered])
            ordered=[k for _,k in sorted(zip(map(float,rank),ordered),reverse=True)]
        except Exception as exc:degraded=True;reason='重排序不可用：'+type(exc).__name__
    evidence=[]
    for cid in ordered[:8]:
        r=lookup[cid]
        evidence.append({'id':cid,'account_id':r['account_id'],'message_id':r['message_id'],'thread_id':r['thread_id'],'text':r['content'],'location':r['location'],'received_at':r['received_at']})
    return {'mode':'keyword' if q.mode!='keyword' and not dense else q.mode,'requested_mode':q.mode,'degraded':degraded,'reason':reason,
            'model_version':version,'reranker_version':model_manifest().get('reranker',{}).get('revision'),
            'evidence':evidence,'rankings':{'keyword':keyword,'vector':dense,'fusion':fusion,'final':ordered[:8]},
            'latency_ms':round((time.monotonic()-started)*1000),'account_ids':accounts}
