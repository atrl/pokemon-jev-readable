#!/usr/bin/env python3
"""Turn actual Jev JSON/PNG traces into an offline replay and a GitHub report.

Does not run a model or an emulator. A green CI job is not game completion.
Supports the original redstar-memory-and-jev artifact without modifying it.
"""

from __future__ import annotations

import argparse
import base64
from collections import Counter
import html
import json
import os
from pathlib import Path
import shutil
import statistics


def read_json(path: Path, default=None):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def position(state: dict | None) -> dict | None:
    player = (state or {}).get("player") or {}
    keys = ("map_id", "x", "y")
    return {k: player[k] for k in keys} if all(k in player for k in keys) else None


def text_rows(state: dict | None) -> list[str]:
    rows = ((state or {}).get("screen_text") or {}).get("rows", [])
    return [str(row) for row in rows if str(row).strip()]


def image_uri(path: Path) -> str | None:
    if not path.is_file():
        return None
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) > 2_000_000:
        raise ValueError(f"Invalid or excessive screenshot: {path.name}")
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def export_results(source: Path, output: Path, *, run_url: str = "") -> dict:
    source, output = Path(source), Path(output)
    game_dir = source / "jev" if (source / "jev").is_dir() else source
    report = read_json(game_dir / "report.json", {})
    # A top-level regression report is not a Jev run report.
    if report.get("policy") == "scripted_regression_not_jev":
        report = {}
    regression = read_json(source / "live" / "report.json", {})
    paths = sorted(game_dir.glob("[0-9][0-9][0-9][0-9]-decision.json"))
    if len(paths) > 1000:
        raise ValueError("At most 1000 decisions per replay")
    steps, frames = [], []
    for path in paths:
        decision = read_json(path)
        if decision.get("source") != "jev":
            continue
        number = int(path.name[:4])
        before = decision.get("request", {}).get("state", {}).get("game", {})
        after_path = game_dir / f"{number:04d}-after.json"
        after = read_json(after_path)
        screenshot = game_dir / f"{number:04d}-before.png"
        answer = decision.get("answer", {})
        response = decision.get("response", {})
        steps.append(
            {
                "step": number,
                "button": answer.get("choice", "unknown"),
                "confidence": answer.get("confidence"),
                "probabilities": answer.get("probabilities", {}),
                "model": response.get("model"),
                "latency_ms": decision.get("latency_ms"),
                "usage": response.get("usage", {}),
                "executed": after is not None,
                "before": position(before),
                "after": position(after),
                "text_before": text_rows(before),
                "text_after": text_rows(after),
                "image": image_uri(screenshot),
                "request": decision.get("request", {}),
            }
        )
        if screenshot.is_file():
            frames.append(screenshot)
    last_image = image_uri(game_dir / "last.png")
    last_state = read_json(game_dir / "last-observation.json", {})
    if last_image:
        frames.append(game_dir / "last.png")
    positions = [p for step in steps for p in (step["before"], step["after"]) if p]
    transitions = sum(
        step["before"] != step["after"] for step in steps if step["before"] and step["after"]
    )
    unique_positions = len({(p["map_id"], p["x"], p["y"]) for p in positions})
    latencies = [s["latency_ms"] for s in steps if isinstance(s["latency_ms"], (float, int))]
    status = report.get("status", "not_run")
    summary = {
        "source_run": run_url,
        "status": status,
        "policy": report.get("policy", "not_run"),
        "planning_calls": report.get("planning_calls", 0),
        "accepted_plans": report.get("plans", 0),
        "planning_failures": report.get("planning_failures", 0),
        "planner_model": report.get("planner_model"),
        "active_plan_status": report.get("active_plan_status"),
        "goal": report.get("goal"),
        "reason": report.get("reason", report.get("error")),
        "reported_jev_calls": report.get("jev_calls", 0),
        "reported_executed_actions": report.get("executed_actions", 0),
        "recorded_decisions": len(steps),
        "recorded_executed_actions": sum(s["executed"] for s in steps),
        "actions": dict(Counter(s["button"] for s in steps)),
        "position_changes": transitions,
        "distinct_positions": unique_positions,
        "start_position": positions[0] if positions else None,
        "end_position": position(last_state) or (positions[-1] if positions else None),
        "mean_model_latency_ms": round(statistics.mean(latencies), 1) if latencies else None,
        "input_tokens": sum(s["usage"].get("input_tokens", 0) for s in steps),
        "output_tokens": sum(s["usage"].get("output_tokens", 0) for s in steps),
        "memory_regression": regression.get("status", "not_available"),
        "game_completion": "not_verified",
        "warning": "本次坐标未改变；可能在对话、菜单或原地循环，不代表任务完成。"
        if positions and not transitions
        else None,
        "replay_timing": "one snapshot per decision, not real-time video",
    }
    output.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": summary,
        "steps": steps,
        "last_image": last_image,
        "last_text": text_rows(last_state),
    }
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    # Untrusted game text must not close a script element or become executable HTML.
    safe_json = encoded.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    template = Path(__file__).with_name("replay.html").read_text(encoding="utf-8")
    (output / "index.html").write_text(
        template.replace("__REPLAY_DATA__", safe_json), encoding="utf-8"
    )
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if frames:
        from PIL import Image

        pictures = []
        for path in frames:
            with Image.open(path) as image:
                pictures.append(image.convert("RGB").resize((480, 432), Image.Resampling.NEAREST))
        pictures[0].save(
            output / "replay.gif",
            save_all=True,
            append_images=pictures[1:],
            duration=800,
            loop=0,
            optimize=False,
        )
        if (game_dir / "last.png").resolve() != (output / "last.png").resolve() and last_image:
            shutil.copyfile(game_dir / "last.png", output / "last.png")

    def md(value):
        return html.escape(str(value)).replace("|", "&#124;").replace("\n", " ")

    lines = ["# Pokémon 本次运行结果", "", "**流程执行结束不等于模型取得进展，更不等于通关。**", ""]
    if run_url:
        lines += [f"来源：{run_url}", ""]
    lines += [
        "| 检查 | 结果 |",
        "|---|---|",
        f"| 停止原因 | `{md(status)}` |",
        f"| DeepSeek 请求 / 接受计划 / 失败 | {summary['planning_calls']} / {summary['accepted_plans']} / {summary['planning_failures']} |",
        f"| Jev 调用 / 已执行动作 | {summary['reported_jev_calls']} / {summary['reported_executed_actions']} |",
        f"| 坐标变化 / 不同位置数 | {transitions} / {unique_positions} |",
        f"| 开始位置 | {md(summary['start_position'])} |",
        f"| 结束位置 | {md(summary['end_position'])} |",
        f"| 按键次数 | {md(summary['actions'])} |",
        "| 任务完成 | 未独立验证 |",
        "",
    ]
    if summary["warning"]:
        lines += ["> " + summary["warning"], ""]
    if summary["reason"]:
        lines += ["说明：" + md(summary["reason"]), ""]
    if frames:
        lines += [
            "## 截图回放",
            "",
            "每步截图按固定间隔播放，**不是实时录像**。",
            "",
            "![逐步截图](replay.gif)",
            "",
        ]
    lines += [
        "## 交互查看",
        "",
        "从 Actions 页面底部下载 **Artifacts**，解压并双击 `index.html`。",
        "可逐步查看截图、按键、概率、前后坐标和完整请求；文件离线可用，不需要启动服务。",
        "",
        "GitHub 中的 HTML 文件默认显示源码，不是预览页面；在 GitHub 上直接看本 README 的 GIF。",
        "",
        "## 动作记录",
        "",
        "| 步骤 | 按键 | 执行前 | 执行后 |",
        "|---|---|---|---|",
    ]
    for step in steps[:100]:
        lines.append(
            f"| {step['step'] + 1} | {md(step['button'])} | {md(step['before'])} | {md(step['after'])} |"
        )
    (output / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-url", default="")
    args = parser.parse_args()
    summary = export_results(args.input, args.output, run_url=args.run_url)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        # No inaccessible local images in the online job summary.
        rows = (args.output / "README.md").read_text(encoding="utf-8").split("## 截图回放")[0]
        with open(target, "a", encoding="utf-8") as stream:
            stream.write(rows + "\n完整回放请下载本次运行的 Artifacts，解压后打开 index.html。\n")


if __name__ == "__main__":
    main()
