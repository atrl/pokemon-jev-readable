"""Budget/runner regressions use explicit offline doubles; no game or API."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import _paths  # noqa: F401
from experiments.common import Budget, BudgetExceeded, DEFAULT_CONFIG, PROTOCOL, ExperimentError
from experiments.runner import compare, run_trial, write_comparison


class BudgetTests(unittest.TestCase):
    def test_replanning_and_provider_changes_do_not_reset_evidence_limit(self):
        budget = Budget({"max_same_state_requests": 2})
        for owner in ("kev", "deepseek"):
            rid = budget.before_request(owner, "unchanged", estimated_tokens=100)
            budget.after_request(rid, {"total_tokens": 20})
        with self.assertRaisesRegex(BudgetExceeded, "same_state"):
            budget.before_request("kev", "unchanged")
        budget.before_request("kev", "new-evidence")

    def test_failed_and_missing_usage_calls_remain_counted_and_reserved(self):
        budget = Budget({"max_requests": 2, "max_tokens": 300})
        rid = budget.before_request("deepseek", "s", estimated_tokens=200)
        budget.after_request(rid, status="http_500")
        with self.assertRaisesRegex(BudgetExceeded, "token_budget"):
            budget.before_request("deepseek", "s", estimated_tokens=101)
        self.assertEqual(budget.snapshot()["unknown_usage_requests"], 1)
        self.assertEqual(budget.snapshot()["measured_tokens"], 0)
        self.assertEqual(budget.charged_tokens, 200)

    def test_measured_provider_usage_and_actual_overrun(self):
        budget = Budget({"max_tokens": 100})
        rid = budget.before_request("kev", "s", estimated_tokens=90)
        budget.after_request(rid, {"input_tokens": 80, "output_tokens": 40})
        self.assertEqual(budget.charged_tokens, 120)
        with self.assertRaisesRegex(BudgetExceeded, "token_budget"):
            budget.check()
        with self.assertRaises(ValueError):
            budget.after_request(rid, {"total_tokens": 1})

    def test_deadline_blocks_late_actions(self):
        now = [0.0]
        budget = Budget({"max_seconds": 1}, clock=lambda: now[0])
        budget.before_action()
        now[0] = 2
        with self.assertRaisesRegex(BudgetExceeded, "wall_time"):
            budget.before_action()


class RunnerTests(unittest.TestCase):
    def _run_with_doubles(self, output, env, controller):
        case = {
            "id": "offline",
            "family": "fixture",
            "split": "test",
            "state_sha256": "fake",
            "rom_sha1": "fake",
        }
        with (
            patch("experiments.suite.load_suite", return_value={"cases": [case]}),
            patch("experiments.suite.get_case", return_value=case),
            patch("experiments.runner.source_digest", return_value="offline-source"),
        ):
            return run_trial(
                "unused",
                "offline",
                "brain",
                output,
                config=DEFAULT_CONFIG,
                allow_model_calls=True,
                env_factory=lambda *a, **kw: env,
                controller_factory=lambda *a: controller,
            )

    def test_failed_decision_still_records_inference_time(self):
        env = Mock()
        env.reset.return_value = {"observation_id": "s", "observation": {}}
        # Mock's dynamic attributes would pretend to implement environment counters.
        del env.executed_actions
        policy = Mock()
        policy.public_config.return_value = {"controller": "brain", "providers": {}}
        policy.decide.side_effect = ExperimentError("model_request_failed")
        with (
            TemporaryDirectory() as tmp,
            patch("experiments.runner.time.monotonic", side_effect=[100.0, 102.75]),
        ):
            output = Path(tmp) / "trial"
            report = self._run_with_doubles(output, env, policy)
            persisted = json.loads((output / "report.json").read_text())
        env.step.assert_not_called()
        self.assertEqual(report["inference_seconds"], 2.75)
        self.assertEqual(persisted["inference_seconds"], 2.75)
        self.assertEqual(report["budget"]["executed_actions"], 0)
        self.assertEqual(report["stop_reason"], "model_request_failed")

    def test_policy_close_failure_does_not_erase_report(self):
        env = Mock()
        env.reset.return_value = {"observation_id": "s", "observation": {}}
        del env.executed_actions
        env.step.return_value = (
            {"observation_id": "next"},
            1.0,
            True,
            False,
            {"success": True, "stop_reason": "success"},
        )
        policy = Mock()
        policy.public_config.return_value = {"controller": "brain", "providers": {}}
        policy.decide.return_value = {"button": "a", "owner": "deepseek", "source": "test_double"}
        policy.close.side_effect = RuntimeError("private cleanup exception")
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "trial"
            report = self._run_with_doubles(output, env, policy)
            persisted = json.loads((output / "report.json").read_text())
        self.assertEqual(report["policy_cleanup_status"], "failed")
        self.assertTrue(persisted["success"])
        self.assertNotIn("private cleanup exception", json.dumps(persisted))

    def test_real_environment_press_is_counted_when_next_observation_fails(self):
        from experiments.environment import ExperimentEnv

        env = ExperimentEnv({}, Path("unused-suite"), rom=Path("unused-rom"))
        env.world = Mock()
        world = env.world
        env._observation = {"observation_id": "before"}
        env.reset = Mock(return_value={"observation_id": "before", "observation": {}})
        env._observe = Mock(side_effect=ExperimentError("observation_failed"))
        policy = Mock()
        policy.public_config.return_value = {"controller": "brain", "providers": {}}
        policy.decide.return_value = {"button": "a", "owner": "deepseek", "source": "test_double"}
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "trial"
            report = self._run_with_doubles(output, env, policy)
            persisted = json.loads((output / "report.json").read_text())
        world.press.assert_called_once_with("a", held=8, settle=16)
        self.assertEqual(report["budget"]["executed_actions"], 1)
        self.assertEqual(report["attempted_actions"], 1)
        self.assertEqual(persisted["stop_reason"], "observation_failed")
        self.assertFalse(report["success"])

    def test_single_initialized_trial_cannot_prove_comparable_inputs(self):
        first = {
            "controller": "brain",
            "case_id": "case",
            "status": "error",
            "success": False,
            "initial_packet_sha256": "hash",
            "directory": "trial-1",
            "inference_seconds": 0.1,
            "budget": {
                "http_requests": 0,
                "measured_tokens": 0,
                "unknown_usage_requests": 0,
                "executed_actions": 0,
            },
        }
        failed_reset = {**first, "controller": "hybrid", "directory": "trial-2"}
        del failed_reset["initial_packet_sha256"]
        for rows in ([first], [first, failed_reset]):
            with self.subTest(trials=len(rows)), TemporaryDirectory() as tmp:
                report = write_comparison(Path(tmp), rows, 2, 0.2)
                check = report["initial_input_checks"]["case"]
                self.assertEqual(check["status"], "insufficient")
                self.assertEqual(check["initialized_trials"], 1)
                self.assertEqual(check["attempted_trials"], len(rows))

    def test_batch_deadline_must_be_positive_and_finite_before_any_work(self):
        for seconds in (float("nan"), float("inf"), -float("inf"), 0, -1, True):
            with (
                self.subTest(seconds=seconds),
                patch("experiments.suite.load_suite") as load,
                patch("experiments.runner.run_trial") as trial,
            ):
                with self.assertRaisesRegex(ValueError, "positive and finite"):
                    compare(
                        "unused",
                        "unused-output",
                        controllers=["brain"],
                        allow_model_calls=True,
                        config=DEFAULT_CONFIG,
                        max_seconds=seconds,
                    )
            load.assert_not_called()
            trial.assert_not_called()

    def test_one_button_owner_and_measured_requests_are_distinct(self):
        case = {
            "id": "offline",
            "family": "fixture",
            "split": "test",
            "state_sha256": "fake",
            "rom_sha1": "fake",
        }
        packet = {
            "protocol": PROTOCOL,
            "observation_id": "s",
            "observation": {},
            "memory": {},
            "goal": "fixture",
        }
        calls = []

        class Env:
            def __init__(self, *args, **kwargs):
                pass

            def reset(self):
                return deepcopy(packet)

            def step(self, button):
                calls.append(button)
                return deepcopy(packet), 1.0, True, False, {"success": True}

            def screenshot(self, path):
                path.write_bytes(b"OFFLINE DOUBLE")

            def close(self):
                pass

        class Controller:
            def decide(self, value):
                return {"button": "a", "owner": "deepseek", "source": "test_double"}

        with (
            TemporaryDirectory() as tmp,
            patch("experiments.suite.load_suite", return_value={"cases": [case]}),
            patch("experiments.suite.get_case", return_value=case),
        ):
            output = Path(tmp) / "trial"
            result = run_trial(
                "unused",
                "offline",
                "brain",
                output,
                config=DEFAULT_CONFIG,
                allow_model_calls=True,
                env_factory=Env,
                controller_factory=lambda *args: Controller(),
            )
            self.assertTrue(result["success"])
            self.assertEqual(calls, ["a"])
            self.assertEqual(result["action_owners"], {"deepseek": 1})
            self.assertEqual(result["budget"]["http_requests"], 0)
            self.assertEqual(result["budget"]["executed_actions"], 1)
            event = json.loads((output / "trajectory.jsonl").read_text())
            self.assertEqual(event["packet"], packet)
            self.assertEqual(event["decision"]["source"], "test_double")

    def test_comparison_retains_failures_and_flags_different_initial_inputs(self):
        rows = [
            {
                "controller": "brain",
                "case_id": "case",
                "status": status,
                "success": status == "success",
                "initial_packet_sha256": str(i),
                "directory": f"trial-{i}",
                "inference_seconds": 0.1,
                "budget": {
                    "http_requests": 1,
                    "measured_tokens": 10,
                    "unknown_usage_requests": 0,
                    "executed_actions": 1,
                },
            }
            for i, status in enumerate(("success", "error"))
        ]
        with TemporaryDirectory() as tmp:
            report = write_comparison(Path(tmp), rows, 2, 0.2)
            self.assertEqual(report["groups"]["brain"]["trials"], 2)
            self.assertEqual(report["groups"]["brain"]["successes"], 1)
            self.assertEqual(report["initial_input_checks"]["case"]["status"], "MISMATCH")
            self.assertTrue((Path(tmp) / "index.html").exists())


if __name__ == "__main__":
    unittest.main()
