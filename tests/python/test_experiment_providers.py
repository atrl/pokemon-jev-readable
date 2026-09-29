"""Offline provider protocol, accounting, and controller separation regressions."""

import io
import json
import threading
import unittest
import urllib.error
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from _paths import ROOT  # noqa: F401
from experiments.common import ACTIONS, Budget, BudgetExceeded, ExperimentError
from experiments.controllers import build_controller, validate_choice, validate_plan
from experiments.providers import Provider, Response, state_key

PACKET = {
    "observation_id": "same-evidence",
    "observation": {"position": [1, 2, 3]},
    "memory": {},
    "goal": "Explore observed surroundings",
    "feedback": {},
}
PLAN = {"intent": "Explore the visible area", "policy": ["Use observed passageways"], "ttl": 3}
CONFIG = {"providers": {"deepseek": {"api_key_env": None}, "kev": {"api_key_env": None}}}


def choice(selected, candidates):
    return {
        "type": "choice",
        "choice": selected,
        "confidence": 0.7,
        "probabilities": {key: 1.0 if key == selected else 0.0 for key in candidates},
    }


def actor_response(button="left", fit="applicable"):
    return {
        "answers": {
            "button": choice(button, ACTIONS),
            "plan_fit": choice(fit, ("applicable", "contradicted", "unknown")),
        },
        "model": "kev-checkpoint",
        "usage": {"input_tokens": 41, "output_tokens": 31},
    }


def chat_response(data, **extra):
    return {
        "choices": [{"message": {"content": json.dumps(data)}, "finish_reason": "stop"}],
        "model": "test-deepseek",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        **extra,
    }


class WireResponse:
    status = 200

    def __init__(self, data):
        self.raw = data if isinstance(data, bytes) else json.dumps(data).encode()

    def read(self, count):
        return self.raw[:count]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_disabled_controller_has_no_network_or_accounting(self):
        budget = Budget()
        controller = build_controller("brain", budget, CONFIG)
        with patch.object(controller.provider._opener, "open") as network:
            with self.assertRaisesRegex(ExperimentError, "model_calls_not_authorized"):
                controller.decide(PACKET)
        network.assert_not_called()
        self.assertEqual(budget.snapshot()["http_requests"], 0)

    def test_protocol_endpoints_models_and_auth_are_independent(self):
        config = {
            "providers": {
                "deepseek": {
                    "base_url": "http://localhost:111/v1",
                    "model": "ds-pin",
                    "api_key_env": "DS_TEST",
                },
                "kev": {
                    "base_url": "http://localhost:222/v1",
                    "model": "kev-pin",
                    "api_key_env": "KEV_TEST",
                },
            }
        }
        with patch.dict("os.environ", {"DS_TEST": "ds-secret", "KEV_TEST": "kev-secret"}):
            budget = Budget()
            deepseek = Provider("deepseek", budget, config, allow_model_calls=True)
            kev = Provider("kev", budget, config, allow_model_calls=True)
            with patch.object(
                deepseek._opener, "open", return_value=WireResponse(chat_response({}))
            ) as ds:
                deepseek.chat("JSON", {"packet": PACKET}, PACKET)
            with patch.object(
                kev._opener, "open", return_value=WireResponse(actor_response())
            ) as local:
                kev.choices(PACKET, {"button": {}}, PACKET)
        ds_req, kev_req = ds.call_args.args[0], local.call_args.args[0]
        self.assertEqual(ds_req.full_url, "http://localhost:111/v1/chat/completions")
        self.assertEqual(kev_req.full_url, "http://localhost:222/v1/systemone")
        self.assertEqual(ds_req.get_header("Authorization"), "Bearer ds-secret")
        self.assertEqual(kev_req.get_header("Authorization"), "Bearer kev-secret")
        self.assertEqual(json.loads(ds_req.data)["model"], "ds-pin")
        self.assertEqual(set(json.loads(kev_req.data)), {"model", "state", "questions"})
        self.assertNotIn("secret", json.dumps(budget.snapshot()))
        self.assertEqual(budget.snapshot()["measured_tokens"], 87)

    def test_public_config_records_resolved_identity_without_secret_values(self):
        config = {
            "providers": {
                "kev": {
                    "base_url": "http://localhost:9000/private-test-key",
                    "model": "kev-4b-pin",
                    "api_key_env": "PRIVATE_KEV_KEY",
                    "checkpoint_sha256": "f" * 64,
                    "api_key": "unrecognized-secret-field",
                }
            }
        }
        with patch.dict("os.environ", {"PRIVATE_KEV_KEY": "private-test-key"}):
            provider = Provider("kev", Budget(), config)
            with patch.object(provider._opener, "open") as network:
                public = provider.public_config()
        network.assert_not_called()
        self.assertEqual(public["api_key_env"], "PRIVATE_KEV_KEY")
        self.assertEqual(public["configured_model"], "kev-4b-pin")
        self.assertEqual(public["declared_checkpoint_sha256"], "f" * 64)
        self.assertIs(public["checkpoint_verified"], False)
        self.assertNotIn("private-test-key", json.dumps(public))
        self.assertNotIn("unrecognized-secret-field", json.dumps(public))

    def test_controller_public_config_has_only_its_participating_providers(self):
        brain = build_controller("brain", Budget(), CONFIG)
        hybrid = build_controller("hybrid", Budget(), CONFIG)
        self.assertEqual(set(brain.public_config()["providers"]), {"deepseek"})
        self.assertEqual(set(hybrid.public_config()["providers"]), {"deepseek", "kev"})
        self.assertEqual(hybrid.public_config()["max_plan_ttl"], 12)

    def test_kev_optional_auth_does_not_require_typesafe_key(self):
        kev = Provider("kev", Budget(), {}, allow_model_calls=True)
        with patch.object(
            kev._opener, "open", return_value=WireResponse(actor_response())
        ) as network:
            kev.choices(PACKET, {}, PACKET)
        self.assertIsNone(network.call_args.args[0].get_header("Authorization"))

    def test_retry_is_another_accounted_request_and_missing_usage_is_reserved(self):
        budget = Budget()
        provider = Provider("kev", budget, CONFIG, allow_model_calls=True)
        failure = urllib.error.HTTPError(
            provider.url, 503, "secret remote message", {}, io.BytesIO(b"secret")
        )
        with patch.object(
            provider._opener, "open", side_effect=[failure, WireResponse({"answers": {}})]
        ) as network:
            provider.choices(PACKET, {}, PACKET)
        self.assertEqual(network.call_count, 2)
        snapshot = budget.snapshot()
        self.assertEqual(snapshot["unknown_usage_requests"], 2)
        self.assertEqual(snapshot["measured_tokens"], 0)
        self.assertGreater(snapshot["charged_tokens"], 0)
        self.assertEqual([x["status"] for x in snapshot["requests"]], ["http_503", "ok"])
        self.assertNotIn("secret", json.dumps(snapshot))

    def test_auth_and_billing_fail_without_retry_or_body_disclosure(self):
        for status in (401, 402, 403):
            with self.subTest(status=status):
                budget = Budget()
                provider = Provider("kev", budget, CONFIG, allow_model_calls=True)
                error = urllib.error.HTTPError(
                    provider.url, status, "SECRET", {}, io.BytesIO(b"SECRET")
                )
                with patch.object(provider._opener, "open", side_effect=error) as network:
                    with self.assertRaisesRegex(ExperimentError, f"kev_http_{status}") as caught:
                        provider.choices(PACKET, {}, PACKET)
                self.assertEqual(network.call_count, 1)
                self.assertNotIn("SECRET", str(caught.exception))
                self.assertEqual(budget.snapshot()["unknown_usage_requests"], 1)

    def test_inconsistent_or_partial_usage_is_unknown_not_zero(self):
        for usage in (
            {"prompt_tokens": 100, "completion_tokens": 4, "total_tokens": 0},
            {"prompt_tokens": 100},
            {"input_tokens": True, "output_tokens": 4},
        ):
            with self.subTest(usage=usage):
                budget = Budget()
                provider = Provider("kev", budget, CONFIG, allow_model_calls=True)
                response = actor_response()
                response["usage"] = usage
                with patch.object(provider._opener, "open", return_value=WireResponse(response)):
                    provider.choices(PACKET, {}, PACKET)
                self.assertEqual(budget.snapshot()["unknown_usage_requests"], 1)
                self.assertGreater(budget.snapshot()["charged_tokens"], 0)

    def test_token_reservation_prevents_network(self):
        provider = Provider("kev", Budget({"max_tokens": 10}), CONFIG, allow_model_calls=True)
        with patch.object(provider._opener, "open") as network:
            with self.assertRaisesRegex(BudgetExceeded, "token_budget"):
                provider.choices(PACKET, {}, PACKET)
        network.assert_not_called()

    def test_response_over_budget_cannot_become_action(self):
        ticks = [0.0]
        budget = Budget({"max_seconds": 5}, clock=lambda: ticks[0])
        provider = Provider("deepseek", budget, CONFIG, allow_model_calls=True)

        def delayed(*args, **kwargs):
            self.assertLessEqual(kwargs["timeout"], 5)
            ticks[0] = 6
            return WireResponse(chat_response({"button": "a", "notes": ""}))

        with patch.object(provider._opener, "open", side_effect=delayed):
            with self.assertRaisesRegex(BudgetExceeded, "wall_time_budget"):
                provider.chat("JSON", PACKET, PACKET)
        self.assertEqual(budget.snapshot()["http_requests"], 1)

    def test_truncated_or_invalid_json_is_not_retried(self):
        provider = Provider("deepseek", Budget(), CONFIG, allow_model_calls=True)
        data = chat_response({"button": "a", "notes": ""})
        data["choices"][0]["finish_reason"] = "length"
        with patch.object(provider._opener, "open", return_value=WireResponse(data)) as network:
            with self.assertRaisesRegex(ExperimentError, "deepseek_invalid_response"):
                provider.chat("JSON", PACKET, PACKET)
        self.assertEqual(network.call_count, 1)

    def test_redirect_does_not_forward_request_or_bearer(self):
        paths = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                paths.append(self.path)
                self.send_response(307)
                self.send_header("Location", "/credential-recipient")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        config = {"kev": {"base_url": f"http://127.0.0.1:{server.server_port}"}}
        provider = Provider("kev", Budget(), config, allow_model_calls=True)
        with self.assertRaisesRegex(ExperimentError, "kev_http_307"):
            provider.choices(PACKET, {}, PACKET)
        self.assertEqual(paths, ["/v1/systemone"])


class ControllerTests(unittest.TestCase):
    def test_brain_emits_one_owned_key_and_keeps_notes_out_of_packet(self):
        packet, saved = deepcopy(PACKET), deepcopy(PACKET)
        controller = build_controller("brain", Budget(), CONFIG)
        result = Response(
            {"button": "a", "notes": "Perhaps the dialogue needs confirmation"}, 1, "ds", {}
        )
        with patch.object(controller.provider, "chat", return_value=result) as request:
            decision = controller.decide(packet)
        self.assertEqual(
            (decision["button"], decision["owner"], decision["source"]), ("a", "deepseek", "model")
        )
        self.assertEqual(packet, saved)
        self.assertEqual(request.call_args.args[1]["private_notes"]["source"], "model_hypothesis")
        controller.reset()
        self.assertEqual(controller.notes, "")

    def test_brain_rejects_macros_and_extra_fields(self):
        controller = build_controller("brain", Budget(), CONFIG)
        for data in (
            {"button": "a", "notes": "", "ui_steps": ["a"]},
            {"button": ["a", "b"], "notes": ""},
            {"button": "a", "notes": "x" * 601},
        ):
            with (
                self.subTest(data=str(data)[:60]),
                patch.object(controller.provider, "chat", return_value=Response(data, 1, "ds", {})),
                self.assertRaises(ExperimentError),
            ):
                controller.decide(PACKET)

    def test_plan_is_strict_and_rejects_execution_shortcuts(self):
        for value in (
            {**PLAN, "ui_steps": ["a"]},
            {**PLAN, "ttl": True},
            {**PLAN, "ttl": 99},
            {**PLAN, "policy": "a,b,a"},
        ):
            with self.assertRaises(ExperimentError):
                validate_plan(value, 12)

    def test_plan_ttl_reuses_planner_but_kev_selects_every_key(self):
        controller = build_controller("hybrid", Budget(), CONFIG)
        with patch.object(
            controller.planner, "chat", return_value=Response(PLAN, 1, "ds", {})
        ) as planner:
            with patch.object(
                controller.actor, "choices", return_value=Response(actor_response(), 2, "kev", {})
            ) as actor:
                decisions = [controller.decide(PACKET) for _ in range(4)]
        self.assertEqual(planner.call_count, 2)
        self.assertEqual(actor.call_count, 4)
        self.assertTrue(all(x["owner"] == "kev" for x in decisions))
        self.assertEqual(decisions[0]["plan"]["source"], "model_hypothesis")

    def test_replan_discards_old_button_and_is_bounded(self):
        controller = build_controller("hybrid", Budget(), CONFIG)
        with patch.object(
            controller.planner, "chat", return_value=Response(PLAN, 1, "ds", {})
        ) as planner:
            with patch.object(
                controller.actor,
                "choices",
                side_effect=[
                    Response(actor_response("a", "contradicted"), 2, "kev", {}),
                    Response(actor_response("b", "applicable"), 4, "kev", {}),
                ],
            ):
                decision = controller.decide(PACKET)
        self.assertEqual(decision["button"], "b")
        self.assertEqual(decision["discarded_requests"], [2])
        self.assertEqual(planner.call_count, 2)
        controller.reset()
        with patch.object(
            controller.planner, "chat", return_value=Response(PLAN, 5, "ds", {})
        ) as planner:
            with patch.object(
                controller.actor,
                "choices",
                return_value=Response(actor_response("a", "unknown"), 6, "kev", {}),
            ) as actor:
                with self.assertRaisesRegex(ExperimentError, "plan_not_applicable"):
                    controller.decide(PACKET)
        self.assertEqual(planner.call_count, 2)
        self.assertEqual(actor.call_count, 2)

    def test_replanning_cannot_reset_shared_same_state_budget(self):
        budget = Budget({"max_same_state_requests": 3})
        controller = build_controller("hybrid", budget, CONFIG, allow_model_calls=True)
        with patch.object(
            controller.planner._opener, "open", return_value=WireResponse(chat_response(PLAN))
        ) as ds:
            with patch.object(
                controller.actor._opener,
                "open",
                return_value=WireResponse(actor_response("a", "unknown")),
            ) as kev:
                with self.assertRaisesRegex(BudgetExceeded, "same_state_request_budget"):
                    controller.decide(PACKET)
        self.assertEqual(ds.call_count + kev.call_count, 3)
        self.assertEqual(budget.snapshot()["http_requests"], 3)

    def test_choice_accepts_kev_normalized_confidence_but_rejects_nan(self):
        answer = choice("a", ACTIONS)
        self.assertEqual(validate_choice(answer, ACTIONS)["confidence"], 0.7)
        answer["confidence"] = float("nan")
        with self.assertRaises(ExperimentError):
            validate_choice(answer, ACTIONS)

    def test_state_identity_does_not_include_model_notes_or_goals(self):
        first = {"observation": {"position": [1, 2, 3]}, "goal": "one", "notes": "one"}
        second = {**first, "goal": "two", "notes": "two", "plan": PLAN}
        self.assertEqual(state_key(first), state_key(second))


if __name__ == "__main__":
    unittest.main()
