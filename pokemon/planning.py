"""System Two bridge: grounded situation -> short plan -> validated contract.

Code decides when a plan is needed from signals and the plan_fit judgment; this module
only builds the situation and performs the request. No button execution lives
here; network failure is reported by the caller and missing data stays unknown.
"""
from __future__ import annotations

from paths import load_prompt
import json
import os
import time
import urllib.request
from urllib.parse import urlsplit

from plan_contract import normalize_plan

DEFAULT_BASE_URL = 'https://api.deepseek.com'
DEFAULT_MODEL = 'deepseek-flash'
# A malformed or unverifiable reply is a model formatting error, not an outage;
# re-ask a bounded number of times before pausing. Never substitute an action.
DEFAULT_PLAN_ATTEMPTS = 3
RETRYABLE_PLAN_ERRORS = ('invalid_plan_or_json', 'empty_response', 'truncated_response',
                         'response_too_large')


def build_situation(observation, campaign, progress):
    from model_context import build_situation as observed_situation
    return observed_situation(observation, campaign, progress)


def planner_configured():
    return bool(os.environ.get('DEEPSEEK_API_KEY', '').strip())


def valid_plan(data, situation=None):
    """Validate a model plan against the exact supplied situation."""
    if situation is None:
        raise ValueError('Observed plans require the exact situation they answer')
    return normalize_plan(data, situation)


# System Two's instructions have one source of truth in prompts/system2/.
PLANNER_SYSTEM_PROMPT = load_prompt("system2/planner.txt")


class PlannerError(RuntimeError):
    """Safe error category; never embed remote response bodies or credentials."""
    def __init__(self, code, http_status=None):
        super().__init__(code)
        self.code, self.http_status = code, http_status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PlannerError('redirect_refused')


def call_planner(situation, goal, *, timeout=45, on_event=None):
    key = os.environ.get('DEEPSEEK_API_KEY', '').strip()
    if not key:
        raise PlannerError('missing_api_key')
    base = (os.environ.get('DEEPSEEK_BASE_URL') or DEFAULT_BASE_URL).rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise PlannerError('invalid_base_url')
    model = (os.environ.get('DEEPSEEK_MODEL') or DEFAULT_MODEL).strip()
    body = {
        'model': model,
        'messages': [{'role': 'system', 'content': PLANNER_SYSTEM_PROMPT},
                     {'role': 'user', 'content': json.dumps({'overall_goal': goal, 'situation': situation}, ensure_ascii=False, allow_nan=False)}],
        'temperature': 0.2, 'max_tokens': 4096, 'response_format': {'type': 'json_object'},
    }
    # Do not silently rename a user-specified model. Official current models
    # support this field; older/custom compatible models need not accept it.
    if model.startswith(('deepseek-flash', 'deepseek-v4')):
        thinking = os.environ.get('DEEPSEEK_THINKING', 'disabled')
        if thinking not in ('enabled', 'disabled'):
            raise PlannerError('invalid_thinking_mode')
        body['thinking'] = {'type': thinking}
    if on_event:
        on_event({'type': 'planner_request', 'request': body, 'model': model,
                  'situation_id': situation.get('situation_id')})
    request = urllib.request.Request(base + '/chat/completions', data=json.dumps(body).encode(),
                                    method='POST', headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'})
    start = time.monotonic()
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise PlannerError('response_too_large')
        payload = json.loads(raw)
        choice = payload['choices'][0]
        if choice.get('finish_reason') == 'length':
            raise PlannerError('truncated_response')
        content = choice['message'].get('content')
        if not isinstance(content, str) or not content.strip():
            raise PlannerError('empty_response')
        if on_event:
            on_event({'type': 'planner_response', 'model': payload.get('model', model),
                      'usage': payload.get('usage'),
                      'latency_ms': round((time.monotonic()-start)*1000), 'content': content})
        plan = valid_plan(json.loads(content), situation)
    except PlannerError:
        raise
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        raise PlannerError('http_error', status) from None
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        if on_event:
            on_event({'type': 'planner_validation_error', 'error': str(exc)[:200]})
        raise PlannerError('invalid_plan_or_json') from None
    except (OSError, TimeoutError):
        raise PlannerError('network_error') from None
    plan.update(model=payload.get('model', model), usage=payload.get('usage'),
                latency_ms=round((time.monotonic()-start)*1000))
    return plan
