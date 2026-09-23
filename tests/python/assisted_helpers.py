"""Explicit legacy comparator for pre-existing strategy regressions.

Default observed-mode behavior is tested in test_model_agency.py. Keeping these
assertions prevents a refactor from silently changing the opt-in assisted mode.
"""
from run import run as _run
from jev import choose as _choose
from prompt import build_request, observation_for_model


def run(*args, **kwargs):
    kwargs.setdefault('knowledge_mode', 'assisted')
    return _run(*args, **kwargs)


def choose(observation, *args, **kwargs):
    return _choose({**observation, 'knowledge_mode': 'assisted'}, *args, **kwargs)
