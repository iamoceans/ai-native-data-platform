"""Closed model actions, bounded repair, bilingual retrieval and bound explanations."""
import json
import uuid
from types import SimpleNamespace as NS
from datetime import timedelta

import pytest
from app.agent.budget import AnalysisBudget, BudgetExceeded
from app.agent.llm import OpenAICompatibleProvider
from app.agent.planner import PlanningSelection, ObservationSelection
from app.agent.semantic import embed, similarity, rank_memories
from app.agent.conversation import explain
from app.ids import utcnow


def adapter(monkeypatch, outputs):
    requests = []
    def post(url, **kwargs):
        requests.append(kwargs['json'])
        content = outputs[len(requests)-1]
        return NS(raise_for_status=lambda: None, json=lambda: {
            'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5}, 'model': 'fixture-model'})
    monkeypatch.setattr('app.agent.llm.httpx.post', post)
    return OpenAICompatibleProvider(base_url='http://fixture/v1', model='fixture', api_key='fixture-only'), requests


def test_format_repair_is_bounded_and_accounts_both_calls(monkeypatch):
    provider, requests = adapter(monkeypatch, ['not json', json.dumps({'dimensions':['country'], 'next_action':'breakdown'})])
    result = provider.generate_structured(messages=[{'role':'user','content':'why'}], schema=PlanningSelection,
                                          budget=AnalysisBudget.start(), max_output_tokens=100)
    assert result.value.dimensions == ['country']
    assert result.usage.input_tokens == 20 and result.usage.output_tokens == 10
    assert result.model_calls == 2
    assert len(requests) == 2
    assert 'not json' not in str(requests[1])


def test_format_repair_cannot_widen_schema_or_retry_forever(monkeypatch):
    provider, requests = adapter(monkeypatch, ['{"tool":"execute_shell"}', '{"tool":"execute_shell"}'])
    with pytest.raises(ValueError, match='after format repair'):
        provider.generate_structured(messages=[], schema=ObservationSelection, budget=AnalysisBudget.start())
    assert len(requests) == 2


def test_repair_respects_remaining_token_budget(monkeypatch):
    provider, requests = adapter(monkeypatch, ['bad', '{"dimensions":[]}'])
    with pytest.raises(BudgetExceeded):
        provider.generate_structured(messages=[], schema=PlanningSelection,
            budget=AnalysisBudget.start(max_output_tokens=100), max_output_tokens=100)
    assert len(requests) == 1


def test_domain_vectors_recall_bilingual_synonyms_and_rank_relevance():
    query = embed('营收下滑集中在哪个地区')
    relevant = NS(statement='Revenue decline concentrates in the US country segment', scope_dimensions=['country'])
    irrelevant = NS(statement='Android release upgraded successfully', scope_dimensions=['app_version'])
    assert similarity(query, embed(relevant.statement)) > similarity(query, embed(irrelevant.statement))
    assert rank_memories([irrelevant, relevant], '营收下滑集中在哪个地区') == [relevant, irrelevant]


def test_action_schemas_cannot_accept_arbitrary_tools():
    with pytest.raises(ValueError):
        PlanningSelection.model_validate({'next_action':'sql', 'dimensions':[]})
    with pytest.raises(ValueError):
        ObservationSelection.model_validate({'next_action':'finish', 'sql':'SELECT 1'})


def test_explanation_numbers_come_from_bound_artifact_not_state(monkeypatch):
    aid, cid = uuid.uuid4(), uuid.uuid4()
    analysis = NS(id=aid, state={'observations': {'baseline':'999999'} })
    monkeypatch.setattr(explain.analyses_repo, 'get_artifact', lambda *a: NS(analysis_id=aid,
        content={'baseline':'100.000', 'current':'80.000', 'change':'-20.000'}))
    result = explain.evidence_template(None, analysis, {'kind':'observed_change', 'calculation_id':str(cid)})
    assert all(value in result for value in ['100.000','80.000','-20.000'])
    assert '999999' not in result
    monkeypatch.setattr(explain.analyses_repo, 'get_artifact', lambda *a: NS(analysis_id=uuid.uuid4(), content={'baseline':'123'}))
    assert '123' not in explain.evidence_template(None, analysis, {'kind':'observed_change','calculation_id':str(cid)})


def test_expired_evidence_is_not_quoted(monkeypatch):
    aid, uid, sid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    analysis = NS(id=aid, user_id=uid, session_id=sid,
        final_report={'evidence_available_until':(utcnow()-timedelta(seconds=1)).isoformat(),
                      'claims':[{'id':'c1','text':'Revenue 100'}]})
    monkeypatch.setattr(explain.analyses_repo, 'get_analysis', lambda *a: analysis)
    quotes, dropped = explain.resolve_references(None, user_id=uid, role_ids=[], session_id=sid,
        references=[NS(analysis_id=aid, claim_id='c1')], focus_analysis_id=None)
    assert quotes == [] and dropped == [f'evidence_expired:{aid}']


def test_semantic_recall_filters_grants_before_ranking_and_limits(monkeypatch):
    from app.agent import memory
    allowed=NS(id=uuid.uuid4(),kind='segment',status='confirmed',statement='Version note',scope_dimensions=['app_version'])
    denied=NS(id=uuid.uuid4(),kind='segment',status='confirmed',statement='营收下滑地区集中',scope_dimensions=['country'])
    identity=uuid.uuid4()
    captured={}
    def rows(session,**kwargs):
        captured.update(kwargs);return [denied,allowed]
    monkeypatch.setattr(memory.memory_repo,'list_for_metric',rows)
    monkeypatch.setattr(memory.memory_repo,'memory_dataset_actions_ok',lambda *a, row, **kw: row.id == allowed.id and kw['user_id']==identity)
    brief,ids=memory.planner_brief(None,metric_key='revenue',allowed_dimensions=['app_version'],
        user_id=identity,question='营收下滑地区集中',limit=1)
    assert ids==[allowed.id] and brief[0]['statement']=='Version note'
    assert captured['user_id']==identity and captured['limit']==200


@pytest.mark.parametrize('original,current',[(None,'team'),('team',None),('team','other')])
def test_extraction_refuses_membership_changes_including_private_to_team(monkeypatch, original, current):
    from app.agent import memory
    from app.repositories import users
    team,other=uuid.uuid4(),uuid.uuid4()
    ids={None:None,'team':team,'other':other}
    task=NS(user_id=uuid.uuid4(),state={'metric_key':'revenue','memory_team_id':str(ids[original]) if original else None})
    monkeypatch.setattr(users,'get_user_for_update',lambda *a: NS(active=True,team_id=ids[current]))
    monkeypatch.setattr(memory,'_record_skip',lambda *a,reason,**kw:{'status':'skipped','reason':reason})
    result=memory._apply_extraction(NS(),task=task,digest={'metric':{},'segment_values':{}},generated=None)
    assert result=={'status':'skipped','reason':'memory_scope_changed'}
