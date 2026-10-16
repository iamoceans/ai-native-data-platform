"""Repeatable real-service load, scope races, reruns and replica state checks."""
from __future__ import annotations
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select, func
from fastapi.testclient import TestClient
from app.config import get_settings
from app.db import session_scope, get_session_factory
from app.models.orm import AnalysisTask, BusinessMemory, ExecutionLease, TaskQueue, AuthRateLimit
from app.repositories import analyses, queue
from app.agent import runner, memory
from app.agent.llm import FakeLLMProvider, ProviderResolution
from tests.integration.test_priority_workflows import platform, SCOPE
from tests.helpers import run_pending_analyses

pytestmark=pytest.mark.integration


def test_defaults_seed_new_sessions_only_and_explicit_skills_win(platform):
    profile=platform.get('/api/v1/auth/me').json()
    old=platform.post('/api/v1/sessions',json={'title':'old'}).json()
    defaults={'metric_key':'fixture_revenue','dimensions':['country'],'skill_mode':'auto'}
    changed=platform.patch('/api/v1/auth/me',json={'expected_settings_version':profile['settings_version'],'analysis_defaults':defaults})
    assert changed.status_code==200,changed.text
    assert changed.json()['analysis_defaults']==defaults
    new=platform.post('/api/v1/sessions',json={'title':'new'}).json()
    assert new['resolved_context']['metric_key']=='fixture_revenue'
    assert new['resolved_context']['dimensions']==['country']
    assert new['resolved_context']['field_origins']['metric_key']=='account_preferences'
    assert 'current_start' not in new['resolved_context'] and 'data_complete_state' not in new['resolved_context']
    assert new['skill_config']['mode']=='auto'
    explicit=platform.post('/api/v1/sessions',json={'title':'explicit','skill_config':{'mode':'off'}}).json()
    assert explicit['skill_config']['mode']=='off'
    assert platform.get('/api/v1/sessions/'+old['id']).json()['resolved_context']==old['resolved_context']
    assert platform.patch('/api/v1/auth/me',json={'expected_settings_version':changed.json()['settings_version'],
        'analysis_defaults':{**defaults,'dimensions':['unavailable']}}).status_code==422


def test_rerun_creates_idempotent_child_and_keeps_original_report(platform):
    sid=platform.post('/api/v1/sessions',json={'title':'original'}).json()['id']
    result=platform.post('/api/v1/analyses',headers={'Idempotency-Key':'original'},json={
        'session_id':sid,'question':'why','context':{**SCOPE,'data_complete':True}})
    aid=result.json()['analysis_id']; run_pending_analyses()
    old=platform.get(f'/api/v1/analyses/{aid}/report').json()
    route=f'/api/v1/analyses/{aid}/rerun'
    assert platform.post(route).status_code==422
    child=platform.post(route,headers={'Idempotency-Key':'rerun'})
    assert child.status_code==202,child.text
    assert platform.post(route,headers={'Idempotency-Key':'rerun'}).json()==child.json()
    run_pending_analyses()
    row=platform.get('/api/v1/analyses/'+child.json()['analysis_id']).json()
    assert row['status']=='PARTIAL'  # completeness must be confirmed again
    assert row['context']['completeness']=='unknown'
    assert row['parent_id']==aid
    assert platform.get(f'/api/v1/analyses/{aid}/report').json()==old


def _run_workers(ids, timeout=90):
    from app.query import scheduler, executor
    from app.runtime import get_result_store
    factory,settings,store=get_session_factory(),get_settings(),get_result_store()
    stop=threading.Event();deadline=time.monotonic()+timeout
    counts={'analysis':0,'query':0}; lock=threading.Lock()
    def work(kind,index):
        while not stop.is_set() and time.monotonic()<deadline:
            with session_scope() as db:
                claim=(queue.claim_next_analysis(db,worker_id=f'load-a-{index}',settings=settings) if kind=='analysis'
                       else scheduler.claim_next(db,worker_id=f'load-q-{index}',settings=settings))
            if claim is None:
                stop.wait(.025);continue
            if kind=='analysis':runner.run_claim(factory,claim=claim,settings=settings)
            else:executor.execute_claim(session_factory=factory,settings=settings,store=store,claim=claim)
            with lock:counts[kind]+=1
    with ThreadPoolExecutor(max_workers=5) as pool:
        jobs=[pool.submit(work,'analysis',i) for i in range(3)]+[pool.submit(work,'query',i) for i in range(2)]
        try:
            while time.monotonic()<deadline:
                for job in jobs:
                    if job.done():job.result()
                with session_scope() as db:
                    states=list(db.scalars(select(AnalysisTask.status).where(AnalysisTask.id.in_(ids))))
                if len(states)==len(ids) and all(state in {'COMPLETED','PARTIAL','FAILED','CANCELLED'} for state in states):break
                stop.wait(.05)
            else:pytest.fail('concurrent analysis load exceeded deadline')
        finally:stop.set()
        for job in jobs:job.result(timeout=15)
    with session_scope() as db:
        assert set(db.scalars(select(AnalysisTask.status).where(AnalysisTask.id.in_(ids))))=={'COMPLETED'}
        assert not db.scalar(select(func.count()).select_from(ExecutionLease).where(ExecutionLease.state=='ACTIVE'))
        assert not db.scalar(select(func.count()).select_from(TaskQueue).where(TaskQueue.state.in_(['READY','LEASED'])))
    return counts


def test_concurrent_sessions_workers_and_sustained_memory_extraction(platform,monkeypatch):
    """SOAK_SECONDS extends the same real query/queue path; no throughput promise."""
    started=time.monotonic();duration=int(os.environ.get('AIND_TEST_SOAK_SECONDS','0'))
    count=int(os.environ.get('AIND_TEST_LOAD_SESSIONS','8'))
    assert 2<=count<=32 and 0<=duration<=3600
    # Competing authors in one team exercise the scope lock, not just the
    # per-user lock. All source queries retain their real author and grants.
    team=platform.post('/api/v1/admin/teams',json={'name':'load-team'}).json()['id']
    owner=platform.get('/api/v1/auth/me').json()['id']
    roles=platform.get('/api/v1/admin/roles').json()
    password='Load-peer-'+uuid.uuid4().hex
    peer=platform.post('/api/v1/admin/users',json={'username':'load-peer','password':password,
        'role_ids':[next(row['id'] for row in roles if row['name']=='admin')]}).json()['id']
    for uid in (owner,peer):
        assert platform.put(f'/api/v1/admin/users/{uid}/team',json={'team_id':team}).status_code==200
    with TestClient(platform.app) as identity:
        logged=identity.post('/api/v1/auth/login',json={'username':'load-peer','password':password})
        assert logged.status_code==200
        identity.headers['X-CSRF-Token']=logged.json()['csrf_token']
        identities=[(platform.cookies,dict(platform.headers)),(identity.cookies,dict(identity.headers))]
    waves=0;all_ids=[];samples=[]
    while True:
        def submit(index):
            with TestClient(platform.app) as client:
                cookies,headers=identities[index%2]
                client.cookies.update(cookies);client.headers.update(headers)
                sid=client.post('/api/v1/sessions',json={'title':f'load-{waves}-{index}'}).json()['id']
                response=client.post('/api/v1/analyses',headers={'Idempotency-Key':str(uuid.uuid4())},json={
                    'session_id':sid,'question':'US revenue movement','context':{**SCOPE,'data_complete':True}})
                assert response.status_code==202,response.text
                return uuid.UUID(response.json()['analysis_id'])
        with ThreadPoolExecutor(max_workers=4) as clients:ids=list(clients.map(submit,range(count)))
        wave_start=time.monotonic();counts=_run_workers(ids);samples.append(time.monotonic()-wave_start)
        all_ids+=ids;waves+=1
        # A warm-up plus a second wave checks queue/capacity reuse by default.
        if waves>=2 and time.monotonic()-started>=duration:break
    # Real finished analyses, distinct extraction claims and DB sessions.
    # Closely related phrasings must not create one memory per concurrent run.
    phrases=['Revenue movement concentrates on US across country and platform groups',
             'Revenue movement across country and platform concentrates on US']
    sequence=iter(range(len(all_ids)));guard=threading.Lock()
    def provider(settings):
        with guard:index=next(sequence)
        return ProviderResolution(provider=FakeLLMProvider([{'memories':[{'kind':'segment','statement':phrases[index%2],'dimensions':['country']}]}]))
    monkeypatch.setattr(memory,'resolve_provider',provider)
    before=time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as extractors:
        results=list(extractors.map(lambda aid:memory.run_extraction(get_session_factory(),analysis_id=aid,settings=get_settings()),all_ids))
    assert all(row['status']=='extracted' for row in results),results
    with session_scope() as db:
        rows=list(db.scalars(select(BusinessMemory)))
        assert len(rows)==1,[row.statement for row in rows]
    summary={'sessions':len(all_ids),'waves':waves,'elapsed_seconds':round(time.monotonic()-started,3),
        'wave_seconds':samples,'memory_seconds':round(time.monotonic()-before,3),'memories':len(rows),'workers':{'analysis':3,'query':2,'memory':4},'authors':2}
    print('LOAD_ACCEPTANCE '+json.dumps(summary))


def test_shared_failure_window_survives_new_limiter_instances_and_expires(bootstrap):
    from app.auth.ratelimit import DatabaseLoginRateLimiter
    def attempt(index):
        with session_scope() as db:
            limiter=DatabaseLoginRateLimiter(db,5,300)
            ok=limiter.check('replica-login','client')
            if ok:limiter.record_failure('replica-login','client')
            return ok
    with ThreadPoolExecutor(max_workers=8) as replicas:
        results=list(replicas.map(attempt,range(16)))
    assert sum(results)==5
    with session_scope() as db:
        row=db.scalar(select(AuthRateLimit));row.expires_at=func.now()-timedelta(seconds=1)
    assert attempt(17)


def test_failure_limit_survives_fresh_api_processes(bootstrap):
    import subprocess,sys
    from pathlib import Path
    script="""
from app.db import session_scope
from app.auth.ratelimit import DatabaseLoginRateLimiter
with session_scope() as db:
    limiter=DatabaseLoginRateLimiter(db,3,300)
    allowed=limiter.check('process-login','same-client')
    if allowed:limiter.record_failure('process-login','same-client')
    print(int(allowed))
"""
    outcomes=[]
    for _ in range(5):
        result=subprocess.run([sys.executable,'-c',script],cwd=Path(__file__).resolve().parents[2],capture_output=True,text=True,timeout=15)
        assert result.returncode==0,result.stderr
        outcomes.append(int(result.stdout.strip()))
    assert outcomes==[1,1,1,0,0]


def test_second_api_instance_replays_db_events_and_session_revocation(platform):
    from app.main import create_app
    from app.api.stream_auth import stream_session_active
    from app.models.orm import AuthSession
    from app.ids import utcnow
    sid=platform.post('/api/v1/sessions',json={'title':'replica stream'}).json()['id']
    result=platform.post('/api/v1/analyses',headers={'Idempotency-Key':'replica-analysis'},json={
        'session_id':sid,'question':'compare','context':{**SCOPE,'data_complete':True}})
    run_pending_analyses()
    with TestClient(create_app()) as second:
        second.cookies.update(platform.cookies)
        body=second.get(f'/api/v1/sessions/{sid}/events').text
        ids=[int(line[4:]) for line in body.splitlines() if line.startswith('id: ')]
        assert ids and ids==sorted(set(ids))
        resumed=second.get(f'/api/v1/sessions/{sid}/events',headers={'Last-Event-ID':str(ids[-1])}).text
        assert 'id: ' not in resumed and 'stream.end' in resumed
    with session_scope() as db:
        auth=db.scalar(select(AuthSession).where(AuthSession.revoked_at.is_(None)))
        assert stream_session_active(db,auth.id,auth.user_id)
        auth.revoked_at=utcnow();db.flush()
        assert not stream_session_active(db,auth.id,auth.user_id)


def test_shared_limiter_capacity_is_bounded_and_expired_slots_are_reclaimed(bootstrap):
    from app.auth.ratelimit import DatabaseLoginRateLimiter
    def check(index):
        with session_scope() as db:
            limiter=DatabaseLoginRateLimiter(db,5,300,max_keys=2)
            return limiter.check('capacity-'+str(index),'client')
    with ThreadPoolExecutor(max_workers=6) as clients:results=list(clients.map(check,range(12)))
    assert sum(results)==2
    with session_scope() as db:
        rows=list(db.scalars(select(AuthRateLimit)));assert len(rows)==2
        for row in rows:row.expires_at=func.now()-timedelta(seconds=1)
    assert check(20)
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(AuthRateLimit))==1
