#!/usr/bin/env python3
"""One-time authorized cleanup: archive exact old heads, require merged ancestry,
then delete with an exact SHA lease. Unrecognized or advanced branches survive.
"""
import base64
import json
import os
import subprocess
import urllib.error
from publish_result import api

TARGETS = {
    'feat/pokemon-redstar-jev': 'b2ac2ee8ed3a63d97a93103bcd7d7687de6303b9',
    'verify/red-star-rom-20260922': 'bc7a974c4ccc941274fa11f78946d0c6d8a9f9c0',
    'test/pokemon-firered': '8e3cc64b7e83a2e63b390584ac24a4a8bb97c044',
}


def ref(path):
    try:
        return api('git/ref/' + path)['object']['sha']
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def main():
    main_sha = ref('heads/main')
    pending = []
    for branch, expected in TARGETS.items():
        current = ref('heads/' + branch)
        if current is None:
            print(branch + ': already absent')
            continue
        if current != expected:
            raise RuntimeError(branch + ' changed; preserving it')
        comparison = api('compare/' + expected + '...' + main_sha)
        if comparison['status'] not in ('ahead', 'identical'):
            raise RuntimeError(branch + ' not merged; preserving it')
        pending.append((branch, expected))
    results = []
    for branch, expected in pending:
        tag = 'archive/pokemon-20260922/' + branch.replace('/', '-')
        tag_sha = ref('tags/' + tag)
        if tag_sha is None:
            api('git/refs', {'ref': 'refs/tags/' + tag, 'sha': expected})
        elif tag_sha != expected:
            raise RuntimeError('Archive tag mismatch; preserving branch')
        token = base64.b64encode(('x-access-token:' + os.environ['GH_TOKEN']).encode()).decode()
        env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_CONFIG_COUNT='1',
                   GIT_CONFIG_KEY_0='http.https://github.com/.extraheader',
                   GIT_CONFIG_VALUE_0='AUTHORIZATION: basic ' + token)
        subprocess.run(['git', 'push', '--force-with-lease=refs/heads/' + branch + ':' + expected,
                        'origin', ':refs/heads/' + branch], env=env, check=True)
        results.append({'branch': branch, 'archived_as': tag, 'sha': expected, 'deleted': True})
    print(json.dumps(results, indent=2))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write('## 分支整理\n\n```json\n' + json.dumps(results, indent=2) + '\n```\n')


if __name__ == '__main__':
    main()
