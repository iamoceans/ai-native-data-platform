"""Child limits remain enforceable across queue waits, retries and worker restarts."""
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import MagicMock
import uuid

import httpx
import pytest

from app.agent.budget import AnalysisBudget, BudgetExceeded
from app.agent.conversation.budget import TurnBudget
from app.agent.llm import OpenAICompatibleProvider, ProviderResolution
from app.agent.planner import PlanningSelection
from app.agent import runner
from app.config import Settings
from app.ids import utcnow


def test_child_inherits_absolute_deadline_and_independent_model_limit():
    settings = Settings(_env_file=None)
    parent = TurnBudget.start(settings, deadline_at=utcnow() + timedelta(seconds=25))
    parent.model_calls = parent.max_model_calls - 1
    child = parent.analysis_sub_budget(settings)
    assert child['deadline_at'] <= parent.deadline_at.isoformat()
    assert child['max_model_calls'] == 1
    assert child['max_tool_calls'] == settings.agent_max_tool_calls
    restored = AnalysisBudget.start(**child)
    assert 0 < restored.remaining_seconds() <= 25
    assert restored.record_model_usage(input_tokens=1, output_tokens=1).consume_tool(queries=1).queries == 1
    with pytest.raises(BudgetExceeded, match='model_calls'):
        restored.record_model_usage(input_tokens=1, output_tokens=1, model_calls=2)


@pytest.mark.parametrize('phase', ['CREATED', 'EXECUTING', 'OBSERVING', 'SYNTHESIZING'])
@pytest.mark.parametrize('dispatch', ['run_claim', 'execute_claim'])
def test_expired_analysis_never_dispatches_any_phase(monkeypatch, phase, dispatch):
    task = NS(id=uuid.uuid4(), status=phase, state={}, budget={
        'deadline_at': (utcnow() - timedelta(seconds=1)).isoformat(),
    })
    claim = NS(analysis_id=task.id)
    factory = MagicMock()
    session = factory.return_value.__enter__.return_value
    monkeypatch.setattr(runner.analyses_repo, 'get_analysis_for_update', lambda *a: task)
    monkeypatch.setattr(runner.queue_repo, 'lock_analysis_claim', lambda *a: object())
    monkeypatch.setattr(runner.queue_repo, 'release_analysis_claim', MagicMock())
    monkeypatch.setattr(runner.analyses_repo, 'set_status', lambda task, status: setattr(task, 'status', status))
    monkeypatch.setattr(runner, '_event', MagicMock())
    phases = [MagicMock() for _ in range(4)]
    for name, method in zip(['_prepare', '_executing', '_observing', '_synthesizing'], phases):
        monkeypatch.setattr(runner, name, method)
    if dispatch == 'run_claim':
        runner.run_claim(factory, claim=claim, settings=Settings(_env_file=None))
    else:
        runner.execute_claim(session, claim=claim, settings=Settings(_env_file=None))
    assert task.status == 'FAILED'
    assert 'wall_seconds' in task.state['last_error']['message']
    for method in phases:
        method.assert_not_called()


def test_legacy_deadline_counts_queue_wait_and_is_not_renewed():
    task = NS(budget={'max_wall_seconds': 30}, created_at=utcnow() - timedelta(seconds=31))
    with pytest.raises(BudgetExceeded, match='wall_seconds'):
        runner._ensure_deadline(task, Settings(_env_file=None))
    assert task.budget['deadline_at'] == (task.created_at + timedelta(seconds=30)).isoformat()


@pytest.mark.parametrize('valid', [True, False])
def test_last_physical_call_is_available_but_cannot_repair(monkeypatch, tmp_path, valid):
    key = tmp_path / 'key'
    key.write_text('fixture-only')
    requests = []
    def post(url, **kwargs):
        requests.append(kwargs)
        return httpx.Response(200, request=httpx.Request('POST', url), json={
            'choices': [{'message': {'content': '{"dimensions":[]}' if valid else '{} invalid'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5},
        })
    monkeypatch.setattr('app.agent.llm.httpx.post', post)
    provider = OpenAICompatibleProvider(base_url='https://fixture.invalid/v1', model='fixture', api_key_file=key)
    budget = AnalysisBudget.start(max_model_calls=2, model_calls=1,
        deadline_at=(utcnow() + timedelta(seconds=2)).isoformat())
    if valid:
        assert provider.generate_structured(messages=[], schema=PlanningSelection, budget=budget, max_output_tokens=50).model_calls == 1
    else:
        with pytest.raises(BudgetExceeded, match='model_calls'):
            provider.generate_structured(messages=[], schema=PlanningSelection, budget=budget, max_output_tokens=50)
    assert len(requests) == 1
    assert 0 < requests[0]['timeout'] <= 2


def test_reclaimed_analysis_cannot_reserve_spent_physical_calls(monkeypatch):
    task = NS(id=uuid.uuid4(), status='OBSERVING', question='fixture', state={}, checkpoint_version=1,
              budget=AnalysisBudget.start(max_model_calls=2, model_calls=2).as_dict())
    claim = NS(analysis_id=task.id)
    factory = MagicMock()
    monkeypatch.setattr(runner.analyses_repo, 'get_analysis_for_update', lambda *a: task)
    monkeypatch.setattr(runner.queue_repo, 'lock_analysis_claim', lambda *a: NS())
    monkeypatch.setattr(runner.queue_repo, 'release_analysis_claim', MagicMock())
    monkeypatch.setattr(runner.analyses_repo, 'set_status', lambda task, status: setattr(task, 'status', status))
    monkeypatch.setattr(runner, '_event', MagicMock())
    monkeypatch.setattr(runner, 'resolve_provider', lambda *a: ProviderResolution(provider=object()))
    call = MagicMock()
    monkeypatch.setattr(runner, '_call_provider', call)
    runner.run_claim(factory, claim=claim, settings=Settings(_env_file=None))
    call.assert_not_called()
    assert task.status == 'FAILED' and 'model_calls' in task.state['last_error']['message']


@pytest.mark.parametrize('calls', [1, 2])
def test_runner_reserves_and_settles_available_physical_attempts(monkeypatch, calls):
    task = NS(id=uuid.uuid4(), question='fixture', status='OBSERVING', state={}, checkpoint_version=1,
              budget=AnalysisBudget.start(max_model_calls=calls).as_dict())
    claim = NS(analysis_id=task.id, fencing_token=1)
    factory = MagicMock()
    monkeypatch.setattr(runner.analyses_repo, 'get_analysis_for_update', lambda *a: task)
    monkeypatch.setattr(runner.queue_repo, 'lock_analysis_claim', lambda *a: NS())
    monkeypatch.setattr(runner.queue_repo, 'release_analysis_claim', MagicMock())
    monkeypatch.setattr(runner, '_event', MagicMock())
    monkeypatch.setattr(runner, 'resolve_provider', lambda *a: ProviderResolution(provider=object()))
    monkeypatch.setattr(runner, '_observing', lambda *a, **k: False)
    def generate(*args, **kwargs):
        assert task.budget['model_calls'] == calls
        assert kwargs['budget'].model_calls == 0
        return NS(usage=NS(input_tokens=10, output_tokens=5), model_calls=calls,
                  value=NS(next_action='finish'), model_id='fixture')
    monkeypatch.setattr(runner, '_call_provider', generate)
    runner.run_claim(factory, claim=claim, settings=Settings(_env_file=None))
    assert task.budget['model_calls'] == calls
    assert task.budget['input_tokens'] == 10
    assert task.budget['output_tokens'] == 5
    assert task.state['observation_action'] == 'finish'


def test_legacy_child_observation_retains_its_original_call_ceiling():
    task = NS(question="fixture", state={}, checkpoint_version=1,
              budget={"max_tool_calls": 1, "model_calls": 1})
    budget = runner._observation_input(task, Settings(_env_file=None))["budget"]
    assert budget.max_model_calls == 1
    with pytest.raises(BudgetExceeded, match="model_calls"):
        budget.reserve_model_call(estimated_input=1, max_output=1)
