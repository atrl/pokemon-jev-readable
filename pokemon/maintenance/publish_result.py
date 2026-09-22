#!/usr/bin/env python3
"""Publish only generated replay files to this private repo; never ROMs/states.

Run on GitHub Actions with a job-scoped contents:write token. Ref updates
are fast-forward-only and retry on concurrent main updates, never force-push.
"""

import argparse
import base64
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request


def api(path, data=None, method=None):
    repo = os.environ["GITHUB_REPOSITORY"]
    request = urllib.request.Request(
        "https://api.github.com/repos/" + repo + "/" + path,
        data=None if data is None else json.dumps(data).encode(),
        headers={
            "Authorization": "Bearer " + os.environ["GH_TOKEN"],
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method=method,
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        body = response.read()
    return json.loads(body) if body else None


def publish(folder: Path, run_id: str, attempt: str):
    if not re.fullmatch(r"[0-9]+", run_id) or not re.fullmatch(r"[0-9]+", attempt):
        raise ValueError("Run and attempt must be numeric")
    summary = json.loads((folder / "summary.json").read_text())
    expected_url = (
        "https://github.com/" + os.environ["GITHUB_REPOSITORY"] + "/actions/runs/" + run_id
    )
    if summary["source_run"] != expected_url:
        raise ValueError("Source run does not match report provenance")
    prefix = f"pokemon/results/runs/{run_id}-{attempt}"
    entries = []
    for name in ("README.md", "summary.json", "replay.gif", "last.png", "index.html"):
        path = folder / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        if len(data) > 8_000_000:
            raise ValueError("Replay too large for repository publication; use the artifact")
        blob = api("git/blobs", {"encoding": "base64", "content": base64.b64encode(data).decode()})
        entries.append(
            {"path": prefix + "/" + name, "mode": "100644", "type": "blob", "sha": blob["sha"]}
        )
    latest = (
        f"# Pokémon 运行结果\n\n[**查看最近一次结果和 GIF**](runs/{run_id}-{attempt}/README.md)\n\n"
        f"来源：{expected_url}\n\n"
        f"停止原因：`{summary['status']}`；Jev 调用 {summary['reported_jev_calls']} 次；"
        f"已执行 {summary['reported_executed_actions']} 次；坐标变化 {summary['position_changes']} 次。\n\n"
        "[历史运行目录](runs/) · [运行说明](../README.md)\n\n"
        "交互回放：从 Actions 的 Artifacts 下载 ZIP，解压打开 index.html。\n"
        "GitHub 直接打开 HTML 会看到源码；本目录的 README 可以直接显示 GIF。\n"
        "绿色工作流不表示完成游戏。回放按决策步骤采样，不是实时直播。\n"
    )
    entries.append(
        {"path": "pokemon/results/README.md", "mode": "100644", "type": "blob", "content": latest}
    )
    for attempt_index in range(3):
        head = api("git/ref/heads/main")["object"]["sha"]
        parent = api("git/commits/" + head)
        tree = api("git/trees", {"base_tree": parent["tree"]["sha"], "tree": entries})
        commit = api(
            "git/commits",
            {
                "message": f"docs(pokemon): publish replay for run {run_id} [skip ci]",
                "tree": tree["sha"],
                "parents": [head],
            },
        )
        try:
            api("git/refs/heads/main", {"sha": commit["sha"], "force": False}, "PATCH")
            break
        except urllib.error.HTTPError as exc:
            if exc.code not in (409, 422) or attempt_index == 2:
                raise
            time.sleep(1)
    url = (
        "https://github.com/"
        + os.environ["GITHUB_REPOSITORY"]
        + "/blob/main/"
        + prefix
        + "/README.md"
    )
    print(json.dumps({"published": url, "commit": commit["sha"]}))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write(f"## 结果已发布\n\n[直接查看 GIF 和动作统计]({url})\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt", default="1")
    args = parser.parse_args()
    publish(args.folder, args.run_id, args.attempt)
