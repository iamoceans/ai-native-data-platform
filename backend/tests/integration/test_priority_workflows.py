"""Priority regressions on isolated real PostgreSQL control/source databases."""
from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.agent import runner
from app.agent.llm import FakeLLMProvider, ProviderResolution
from app.api.deps import get_settings_dep
from app.config import get_settings
from app.db import get_session_factory, session_scope
from app.ids import utcnow
from app.metrics.registry import MetricDefinitionModel, sync_metric_definitions
from app.models.orm import AgentMessage, AnalysisTask, TaskQueue
from app.repositories import analyses as analyses_repo, conversation as conversation_repo, queue as queue_repo
from tests.helpers import setup_datasource_and_grants

pytestmark = pytest.mark.integration

SCOPE = {"metric_key": "fixture_revenue", "baseline_start": "2026-09-01", "baseline_end": "2026-09-02",
         "current_start": "2026-09-02", "current_end": "2026-09-03", "dimensions": ["country"], "filters": {"country": "US"}}


@pytest.fixture
def platform(admin_client, integration_env):
    setup_datasource_and_grants(admin_client, integration_env["source_url"], grant_roles=["admin"])
    with session_scope() as db:
        sync_metric_definitions(db, [MetricDefinitionModel.model_validate({
            "metric_key": "fixture_revenue", "version": 1, "name": "Fixture revenue", "dataset": "public.fixture_metrics",
            "components": {"revenue": {"aggregation": "sum", "column": "revenue_usd"}}, "formula": "revenue",
            "unit": "USD", "aggregation_kind": "sum", "allowed_dimensions": ["country", "platform"],
        })])
    settings = get_settings()
    old = settings.conversational_ask_enabled
    settings.conversational_ask_enabled = True
    admin_client.app.dependency_overrides[get_settings_dep] = lambda: settings
    yield admin_client
    admin_client.app.dependency_overrides.pop(get_settings_dep, None)
    settings.conversational_ask_enabled = old


def analysis(client):
    sid = client.post("/api/v1/sessions", json={"title": "planning"}).json()["id"]
    response = client.post("/api/v1/analyses", headers={"Idempotency-Key": str(uuid.uuid4())},
        json={"session_id": sid, "question": "compare", "context": {**SCOPE, "data_complete": True}})
    assert response.status_code == 202, response.text
    with session_scope() as db:
        claim = queue_repo.claim_next_analysis(db, worker_id="planning-first", settings=get_settings())
    return uuid.UUID(response.json()["analysis_id"]), claim


@pytest.mark.parametrize("raises", [False, True])
def test_cancel_during_model_call_is_responsive_and_late_replies_are_discarded(platform, monkeypatch, raises):
    aid, claim = analysis(platform)
    entered, release = threading.Event(), threading.Event()
    class Blocking(FakeLLMProvider):
        def generate_structured(self, **kwargs):
            entered.set()
            assert release.wait(8)
            if raises:
                raise RuntimeError("late endpoint failure")
            return super().generate_structured(**kwargs)
    provider = Blocking([{"dimensions": ["country"]}])
    monkeypatch.setattr(runner, "resolve_provider", lambda settings: ProviderResolution(provider=provider))
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(runner.run_claim, get_session_factory(), claim=claim, settings=get_settings())
        try:
            assert entered.wait(5)
            # This would time out if the model call retained the task lock.
            with session_scope() as db:
                db.execute(text("SET LOCAL lock_timeout='500ms'"))
                task = analyses_repo.get_analysis_for_update(db, aid)
                assert task.status == "UNDERSTANDING"
                assert task.budget["output_tokens"] == 1000
            cancelled = platform.post(f"/api/v1/analyses/{aid}/cancel")
            assert cancelled.status_code == 200, cancelled.text
        finally:
            release.set()
        assert future.result(timeout=5) is False
    with session_scope() as db:
        task = db.get(AnalysisTask, aid)
        assert task.status == "CANCELLED"
        assert not analyses_repo.list_queries(db, aid)


def test_reclaim_fences_old_worker_and_retains_crashed_call_budget(platform, monkeypatch):
    aid, claim = analysis(platform)
    entered, release = threading.Event(), threading.Event()
    class Blocking(FakeLLMProvider):
        def generate_structured(self, **kwargs):
            entered.set()
            assert release.wait(8)
            return super().generate_structured(**kwargs)
    first = Blocking([{"dimensions": ["country"]}])
    monkeypatch.setattr(runner, "resolve_provider", lambda settings: ProviderResolution(provider=first))
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(runner.run_claim, get_session_factory(), claim=claim, settings=get_settings())
        try:
            assert entered.wait(5)
            with session_scope() as db:
                task = analyses_repo.get_analysis_for_update(db, aid)
                reserved = dict(task.budget)
                queue = db.get(TaskQueue, claim.queue_id)
                queue.lease_until = utcnow() - timedelta(seconds=1)
            with session_scope() as db:
                newer = queue_repo.claim_next_analysis(db, worker_id="planning-recovered", settings=get_settings())
            assert newer.fencing_token > claim.fencing_token
            second = FakeLLMProvider([{"dimensions": ["country"]}])
            monkeypatch.setattr(runner, "resolve_provider", lambda settings: ProviderResolution(provider=second))
            assert runner.run_claim(get_session_factory(), claim=newer, settings=get_settings())
        finally:
            release.set()
        assert future.result(timeout=5) is False
    with session_scope() as db:
        task = db.get(AnalysisTask, aid)
        assert task.status == "EXECUTING"
        assert len(analyses_repo.list_queries(db, aid)) == 2
        assert task.budget["input_tokens"] > reserved["input_tokens"]
        assert task.budget["output_tokens"] > reserved["output_tokens"]
        assert task.budget["model_calls"] == 3


def test_latest_and_older_pages_are_stable_under_appends_and_cursor_scope(platform):
    sid = platform.post("/api/v1/sessions", json={"title": "long history"}).json()["id"]
    with session_scope() as db:
        for index in range(125):
            analyses_repo.add_message(db, session_id=uuid.UUID(sid), role="user", content={"text": f"message {index}"})
    path = f"/api/v1/sessions/{sid}/messages"
    newest = platform.get(path, params={"direction": "backward", "limit": 40}).json()
    assert [m["sequence"] for m in newest["items"]] == list(range(86, 126))
    with session_scope() as db:
        analyses_repo.add_message(db, session_id=uuid.UUID(sid), role="user", content={"text": "arrived while reading"})
    cursor = newest["next_cursor"]
    all_messages = list(newest["items"])
    while cursor:
        response = platform.get(path, params={"direction": "backward", "limit": 40, "cursor": cursor})
        assert response.status_code == 200, response.text
        body = response.json()
        all_messages = body["items"] + all_messages
        cursor = body["next_cursor"]
    assert [m["sequence"] for m in all_messages] == list(range(1, 126))
    forward = platform.get(path, params={"limit": 40, "cursor": newest["next_cursor"]})
    assert forward.status_code == 422
    other = platform.post("/api/v1/sessions", json={"title": "other"}).json()["id"]
    assert platform.get(f"/api/v1/sessions/{other}/messages", params={"direction": "backward", "cursor": newest["next_cursor"]}).status_code == 422


def test_retry_is_new_idempotent_work_with_original_scope_not_new_session_scope(platform):
    sid = platform.post("/api/v1/sessions", json={"title": "retry"}).json()["id"]
    original = platform.post(f"/api/v1/sessions/{sid}/turns", headers={"Idempotency-Key": "original"},
        json={"text": "original question", "scope_patch": SCOPE, "skill_selection": {"mode": "off"}}).json()
    tid = original["id"]
    assert platform.post(f"/api/v1/sessions/{sid}/turns/{tid}/retry", headers={"Idempotency-Key": "too-early"}).status_code == 409
    assert platform.post(f"/api/v1/sessions/{sid}/turns/{tid}/cancel").status_code == 200
    with session_scope() as db:
        row = analyses_repo.get_session_for_update(db, uuid.UUID(sid))
        analyses_repo.update_session_scope(db, row, resolved_context={**SCOPE, "filters": {"country": "DE"}, "current_start": "2026-10-01"})
    path = f"/api/v1/sessions/{sid}/turns/{tid}/retry"
    assert platform.post(path).status_code == 422
    other = platform.post("/api/v1/sessions", json={"title": "other"}).json()["id"]
    assert platform.post(f"/api/v1/sessions/{other}/turns/{tid}/retry", headers={"Idempotency-Key": "wrong-session"}).status_code == 404
    retried = platform.post(path, headers={"Idempotency-Key": "retry-click"})
    assert retried.status_code == 202, retried.text
    replay = platform.post(path, headers={"Idempotency-Key": "retry-click"})
    assert replay.json()["id"] == retried.json()["id"] != tid
    assert platform.post(path, headers={"Idempotency-Key": "another-click"}).status_code == 409
    with session_scope() as db:
        current = conversation_repo.get_turn(db, uuid.UUID(retried.json()["id"]))
        assert current.context_before["filters"] == {"country": "US"}
        assert current.context_before["current_start"] == "2026-09-02"
        assert db.get(AgentMessage, current.user_message_id).content["retry_of_turn_id"] == tid
        assert conversation_repo.get_turn(db, uuid.UUID(tid)).status == "CANCELLED"
    messages = platform.get(f"/api/v1/sessions/{sid}/messages", params={"direction": "backward"}).json()["items"]
    assert messages[0]["turn_status"] == "CANCELLED"


def test_memory_extraction_runs_without_task_lock_and_fences_an_expired_extractor(platform, monkeypatch):
    from app.agent import memory
    from app.agent.state import AnalysisStatus
    aid, _claim = analysis(platform)
    with session_scope() as db:
        task = db.get(AnalysisTask, aid)
        # Fixture represents an already published report; extraction never owns
        # or changes the analysis outcome.
        task.status = AnalysisStatus.COMPLETED
        task.state = {"metric_key": "fixture_revenue", "metric_definition": {
            "name": "Revenue", "unit": "USD", "allowed_dimensions": ["country"], "datasets": ["public.fixture_metrics"],
        }}
        task.final_report = {"claims": [], "limitations": []}
    entered, release = threading.Event(), threading.Event()
    class Blocking(FakeLLMProvider):
        def generate_structured(self, **kwargs):
            entered.set()
            assert release.wait(8)
            return super().generate_structured(**kwargs)
    first = Blocking([{"memories": []}])
    monkeypatch.setattr(memory, "resolve_provider", lambda settings: ProviderResolution(provider=first))
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(memory.run_extraction, get_session_factory(), analysis_id=aid, settings=get_settings())
        try:
            assert entered.wait(5)
            with session_scope() as db:
                db.execute(text("SET LOCAL lock_timeout='500ms'"))
                task = analyses_repo.get_analysis_for_update(db, aid)
                assert task.state["memory_extraction"]["status"] == "extracting"
            busy = memory.run_extraction(get_session_factory(), analysis_id=aid, settings=get_settings())
            assert busy["reason"] == "extraction_busy"
            with session_scope() as db:
                task = analyses_repo.get_analysis_for_update(db, aid)
                record = task.state["memory_extraction"]
                task.state = {**task.state, "memory_extraction": {**record, "lease_until": (utcnow() - timedelta(seconds=1)).isoformat()}}
            second = FakeLLMProvider([{"memories": []}])
            monkeypatch.setattr(memory, "resolve_provider", lambda settings: ProviderResolution(provider=second))
            assert memory.run_extraction(get_session_factory(), analysis_id=aid, settings=get_settings())["status"] == "extracted"
        finally:
            release.set()
        assert future.result(timeout=5)["reason"] == "extraction_claim_lost"
    with session_scope() as db:
        task = db.get(AnalysisTask, aid)
        assert task.status == AnalysisStatus.COMPLETED
        assert task.state["memory_extraction"]["attempts"] == 2
        assert len([step for step in analyses_repo.list_steps(db, aid) if step.step_key == "business_memory"]) == 1


def test_missing_manual_skill_inputs_clarify_and_never_override_user_clears(platform):
    from app.agent.conversation import orchestrator
    sid = platform.post("/api/v1/sessions", json={"title": "required inputs"}).json()["id"]
    skill = b'''---
name: required-dimensions
description: Require explicit dimensions before analysis.
metadata:
  askdata:
    schema_version: 1
    metric_keys: [fixture_revenue]
    required_inputs: [dimensions]
---
# Method
Ask for the required scope before analysing.
'''
    uploaded = platform.post("/api/v1/skills", files={"file": ("SKILL.md", skill, "text/markdown")})
    assert uploaded.status_code == 201, uploaded.text
    version = uploaded.json()["latest_version"]["id"]
    submitted = platform.post(f"/api/v1/sessions/{sid}/turns", headers={"Idempotency-Key": "required"}, json={
        "text": "compare totals only", "scope_patch": {**SCOPE, "dimensions": [], "clear": ["dimensions"]},
        "skill_selection": {"mode": "manual", "version_ids": [version]},
    })
    assert submitted.status_code == 202, submitted.text
    tid = uuid.UUID(submitted.json()["id"])
    assert orchestrator.run_turn(get_session_factory(), turn_id=tid, settings=get_settings()) == "WAITING_INPUT"
    with session_scope() as db:
        turn = conversation_repo.get_turn(db, tid)
        assert conversation_repo.analysis_for_turn(db, tid) is None
        content = db.get(AgentMessage, turn.assistant_message_id).content
        assert "dimensions" in content["missing"]
        assert any("明确清空" in question for question in content["questions"])


@pytest.mark.parametrize('action,expected', [('compare', []), ('breakdown', ['country'])])
def test_model_selects_initial_action_and_then_finishes_outside_transactions(platform, monkeypatch, action, expected):
    from tests.helpers import run_pending_analyses
    from app.agent.planner import ObservationSelection
    sid = platform.post('/api/v1/sessions', json={'title':'model actions'}).json()['id']
    response = platform.post('/api/v1/analyses', headers={'Idempotency-Key':'model-actions'}, json={
        'session_id':sid,'question':'compare revenue', 'context':{**SCOPE,'dimensions':[], 'data_complete':True}})
    aid = uuid.UUID(response.json()['analysis_id'])
    seen = []
    class Provider(FakeLLMProvider):
        def generate_structured(self, **kwargs):
            seen.append(kwargs['schema'].__name__)
            with session_scope() as db:
                db.execute(text("SET LOCAL lock_timeout='500ms'"))
                task = analyses_repo.get_analysis_for_update(db, aid)
                assert task.status in {'UNDERSTANDING', 'OBSERVING'}
            return super().generate_structured(**kwargs)
    provider = Provider([{'dimensions':['country'], 'next_action':action}, {'next_action':'finish'}])
    monkeypatch.setattr(runner, 'resolve_provider', lambda settings: ProviderResolution(provider=provider))
    run_pending_analyses()
    with session_scope() as db:
        task = db.get(AnalysisTask, aid)
        assert task.status == 'COMPLETED', task.state
        assert task.state['dimensions'] == expected
        assert task.state['observation_action'] == 'finish'
        assert task.budget['model_calls'] == 2
        assert len(analyses_repo.list_queries(db, aid)) == 2
    assert seen == ['PlanningSelection', 'ObservationSelection']


def test_memory_team_scope_list_counts_curation_and_historical_reads(platform, monkeypatch):
    from tests.helpers import run_pending_analyses
    from fastapi.testclient import TestClient
    from app.repositories import memory, teams, users
    from app.models.orm import BusinessMemory, User
    # Build real query dependencies, then attach memory to their governed evidence.
    aid, claim = analysis(platform)
    runner.run_claim(get_session_factory(), claim=claim, settings=get_settings())
    from tests.helpers import drain_worker
    drain_worker()
    run_pending_analyses()
    owner_id = uuid.UUID(platform.get('/api/v1/auth/me').json()['id'])
    one = platform.post('/api/v1/admin/teams', json={'name':'Team One'})
    two = platform.post('/api/v1/admin/teams', json={'name':'Team Two'})
    assert one.status_code == two.status_code == 201
    t1,t2 = uuid.UUID(one.json()['id']), uuid.UUID(two.json()['id'])
    assert platform.post('/api/v1/admin/teams', json={'name':'Team One'}).status_code == 409
    roles = platform.get('/api/v1/admin/roles').json()
    role_id = next(row['id'] for row in roles if row['name'] == 'admin')
    credentials = [('same-team','fixture-team-password1',t1), ('other-team','fixture-team-password2',t2)]
    others=[]
    for name,password,team_id in credentials:
        created = platform.post('/api/v1/admin/users', json={'username':name,'password':password,'role_ids':[role_id]})
        assert created.status_code == 201, created.text
        uid=created.json()['id']; others.append(uid)
        assert platform.put(f'/api/v1/admin/users/{uid}/team',json={'team_id':str(team_id)}).status_code == 200
    assert platform.put(f'/api/v1/admin/users/{owner_id}/team',json={'team_id':str(t1)}).status_code == 200
    with session_scope() as db:
        row,_ = memory.upsert(db, metric_key='fixture_revenue',kind='segment',statement='US revenue declines',
            dedupe_key='team-test',analysis_id=aid,owner_user_id=owner_id,team_id=t1)
        mid=row.id
        private,_=memory.upsert(db, metric_key='fixture_revenue',kind='segment',statement='US private note',
            dedupe_key='private-test',analysis_id=aid,owner_user_id=owner_id)
        pid=private.id
        task=db.get(AnalysisTask,aid);task.state={**task.state,'memory_used_ids':[str(mid)]}
    assert {row['id'] for row in platform.get('/api/v1/memory').json()['items']} == {str(mid),str(pid)}
    for index,(name,password,_) in enumerate(credentials):
        with TestClient(platform.app) as reader:
            logged = reader.post('/api/v1/auth/login',json={'username':name,'password':password})
            assert logged.status_code == 200
            reader.headers['X-CSRF-Token'] = logged.json()['csrf_token']
            body=reader.get('/api/v1/memory').json()
            assert [row['id'] for row in body['items']] == ([str(mid)] if index == 0 else [])
            assert body['counts'] == ({'proposed':1} if index == 0 else {})
            status=reader.post(f'/api/v1/memory/{mid}/confirm').status_code
            assert status == (200 if index == 0 else 404)
    # Leaving the team closes list/curation AND historical analysis-memory reads.
    platform.put(f'/api/v1/admin/users/{owner_id}/team',json={'team_id':str(t2)})
    assert [row['id'] for row in platform.get('/api/v1/memory').json()['items']] == [str(pid)]
    assert platform.post(f'/api/v1/memory/{mid}/reject').status_code == 404
    assert platform.get(f'/api/v1/analyses/{aid}/memory').json()['used'] == []
    with session_scope() as db:
        assert memory.list_for_metric(db,metric_key='fixture_revenue',user_id=None) == []


@pytest.mark.parametrize('action,expected_queries', [('finish',2),('driver_decomposition',4)])
def test_model_can_choose_or_decline_declared_driver_tool(platform, monkeypatch, action, expected_queries):
    from tests.helpers import run_pending_analyses
    from app.models.orm import MetricDefinition
    with session_scope() as db:
        row=db.scalar(select(MetricDefinition).where(MetricDefinition.metric_key=='fixture_revenue'))
        row.definition={**row.definition,'driver_decomposition':{'impressions_metric':'fixture_impressions'}}
        sync_metric_definitions(db,[MetricDefinitionModel.model_validate({
            'metric_key':'fixture_impressions','version':1,'name':'Impressions','dataset':'public.fixture_metrics',
            'components':{'impressions':{'aggregation':'sum','column':'impressions'}},'formula':'impressions',
            'unit':'count','aggregation_kind':'sum','allowed_dimensions':['country','platform']})])
    provider=FakeLLMProvider([{'dimensions':['country'],'next_action':'driver_decomposition'}, {'next_action':action}])
    monkeypatch.setattr(runner,'resolve_provider',lambda settings: ProviderResolution(provider=provider))
    sid=platform.post('/api/v1/sessions',json={'title':'driver selection'}).json()['id']
    response=platform.post('/api/v1/analyses',headers={'Idempotency-Key':'choose-drivers'},json={
        'session_id':sid,'question':'why revenue changed', 'context':{
            **SCOPE,'current_start':'2026-09-09','current_end':'2026-09-11','data_complete':True}})
    aid=uuid.UUID(response.json()['analysis_id'])
    run_pending_analyses()
    with session_scope() as db:
        task=db.get(AnalysisTask,aid)
        assert task.status=='COMPLETED', task.state
        assert task.state['observation_action']==action
        assert len(analyses_repo.list_queries(db,aid))==expected_queries
        assert any(step['kind']=='driver_decomposition' for step in task.plan['steps']) == (expected_queries==4)


@pytest.mark.parametrize('raises',[False,True])
def test_cancel_during_observation_decision_fences_late_reply(platform, monkeypatch, raises):
    from tests.helpers import drain_worker
    aid,claim=analysis(platform)
    initial=FakeLLMProvider([{'dimensions':['country']}])
    monkeypatch.setattr(runner,'resolve_provider',lambda settings: ProviderResolution(provider=initial))
    runner.run_claim(get_session_factory(),claim=claim,settings=get_settings())
    drain_worker()
    with session_scope() as db:
        claim=queue_repo.claim_next_analysis(db,worker_id='observe-evidence',settings=get_settings())
    runner.run_claim(get_session_factory(),claim=claim,settings=get_settings())
    with session_scope() as db:
        task=db.get(AnalysisTask,aid)
        assert task.status=='OBSERVING'
        claim=queue_repo.claim_next_analysis(db,worker_id='observe-decision',settings=get_settings())
    entered,release=threading.Event(),threading.Event()
    class Blocking(FakeLLMProvider):
        def generate_structured(self,**kwargs):
            entered.set(); assert release.wait(8)
            if raises: raise RuntimeError('late observation failure')
            return super().generate_structured(**kwargs)
    provider=Blocking([{'next_action':'finish'}])
    monkeypatch.setattr(runner,'resolve_provider',lambda settings: ProviderResolution(provider=provider))
    with ThreadPoolExecutor(max_workers=1) as executor:
        future=executor.submit(runner.run_claim,get_session_factory(),claim=claim,settings=get_settings())
        try:
            assert entered.wait(5)
            with session_scope() as db:
                db.execute(text("SET LOCAL lock_timeout='500ms'"))
                task=analyses_repo.get_analysis_for_update(db,aid)
                assert task.status=='OBSERVING'
            assert platform.post(f'/api/v1/analyses/{aid}/cancel').status_code==200
        finally:
            release.set()
        assert future.result(timeout=5) is False
    with session_scope() as db:
        task=db.get(AnalysisTask,aid)
        assert task.status=='CANCELLED'
        assert task.final_report is None
        assert 'observation_action' not in task.state


def test_team_management_requires_admin_and_csrf(admin_client, viewer):
    from fastapi.testclient import TestClient
    with TestClient(admin_client.app) as reader:
        logged=reader.post('/api/v1/auth/login',json=viewer)
        assert logged.status_code==200
        reader.headers['X-CSRF-Token']=logged.json()['csrf_token']
        assert reader.get('/api/v1/admin/teams').status_code==403
        assert reader.post('/api/v1/admin/teams',json={'name':'Forbidden'}).status_code==403
        uid=reader.get('/api/v1/auth/me').json()['id']
        assert reader.put(f'/api/v1/admin/users/{uid}/team',json={'team_id':None}).status_code==403
    original=admin_client.headers.pop('X-CSRF-Token')
    try:
        assert admin_client.post('/api/v1/admin/teams',json={'name':'No csrf'}).status_code==403
    finally:
        admin_client.headers['X-CSRF-Token']=original
