"""Run with python3 -m unittest discover -s tests -v.

Exercise daily_qa.sh with fake tools. No Docker, AI, GitHub or Slack calls.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FAKE_TOOL = r'''
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
log = pathlib.Path(os.environ['QA_TEST_LOG'])
previous = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
event = name
if name == 'python3':
    if args[:2] != ['-m', 'unittest']:
        os.execv(sys.executable, [sys.executable, *args])
    event = 'script-tests'
if name == 'git':
    event = 'git:' + args[0]
elif name == 'docker':
    if 'build' in args:
        event = 'build'
    elif 'up' in args:
        event = 'up'
    elif 'alembic' in args:
        event = 'migrate'
    elif 'pytest' in args:
        event = 'pytest'
    elif 'unittest' in args:
        event = 'script-tests'
elif name == 'codex':
    prompt = args[-1]
    event = 'repair' if '開発用のテスト' in prompt else ('audit' if '本日の自動修正' in prompt else 'save')
with log.open('a') as f:
    f.write(json.dumps(dict(event=event, args=args)) + '\n')
count = sum(row['event'] == event for row in previous) + 1
fail = os.environ.get('QA_TEST_FAIL', '')
if fail in (event, event + str(count)):
    print('simulated failure: ' + event)
    sys.exit(int(os.environ.get('QA_TEST_EXIT', '1')))
if event == 'git:status':
    if os.environ.get('QA_TEST_START_DIRTY') or (count > 1 and os.environ.get('QA_TEST_DIRTY')):
        print(' M saas/src/example.py')
    sys.exit(0)
if event == 'git:symbolic-ref':
    print(os.environ.get('QA_TEST_BRANCH', 'main'))
    sys.exit(0)
if event == 'git:rev-parse':
    print('a' * 40)
    sys.exit(0)
if event == 'git:ls-remote':
    print(('b' if os.environ.get('QA_TEST_UNPUSHED') else 'a') * 40 + '\trefs/heads/main')
    sys.exit(0)
if event == 'repair' and os.environ.get('QA_TEST_QUOTA_TEXT'):
    print('test_video_quota_route PASSED; rate limit handling verified')
else:
    print('ok: ' + event)
'''


class DailyQATests(unittest.TestCase):
    def run_script(self, fail='', code=1, quota_text=False, dirty=False, unpushed=False, branch='main', start_dirty=False, model=''):
        with tempfile.TemporaryDirectory(prefix='tunagumo-qa-test-') as tmp:
            root = Path(tmp)
            shutil.copy2(ROOT / 'daily_qa.sh', root / 'daily_qa.sh')
            (root / 'saas/docker').mkdir(parents=True)
            (root / 'saas/.env.test').write_text('TEST_ONLY=1\n')
            (root / '.env').write_text('SLACK_WEBHOOK_URL=https://example.invalid/test-only\n')
            fake_bin = root / 'bin'
            fake_bin.mkdir()
            for name in ('git', 'docker', 'codex', 'curl', 'run', 'up', 'python3'):
                executable = fake_bin / name
                executable.write_text('#!' + sys.executable + '\n' + FAKE_TOOL)
                executable.chmod(0o755)
            log = root / 'calls.jsonl'
            env = {**os.environ, 'PATH': str(fake_bin) + os.pathsep + os.environ['PATH'],
                   'CODEX_SCHEDULED_MODEL': model, 'QA_TEST_LOG': str(log), 'QA_TEST_FAIL': fail, 'QA_TEST_EXIT': str(code),
                   'QA_TEST_QUOTA_TEXT': '1' if quota_text else '',
                   'QA_TEST_BRANCH': branch, 'QA_TEST_START_DIRTY': '1' if start_dirty else '',
                   'QA_TEST_DIRTY': '1' if dirty else '', 'QA_TEST_UNPUSHED': '1' if unpushed else ''}
            result = subprocess.run(['bash', str(root / 'daily_qa.sh')], env=env,
                                    capture_output=True, text=True, timeout=15)
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            return result, calls

    def assert_stopped(self, fail, forbidden, code=1):
        result, calls = self.run_script(fail=fail, code=code)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        events = [call['event'] for call in calls]
        for event in forbidden:
            self.assertNotIn(event, events)
        self.assertNotIn('正常終了 ===', result.stdout)

    def test_success_verifies_tests_after_audit_before_save(self):
        result, calls = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call['event'] for call in calls], [
            'git:symbolic-ref', 'git:status', 'git:pull', 'build', 'up', 'migrate', 'repair', 'audit',
            'build', 'migrate', 'pytest', 'script-tests', 'save',
            'git:status', 'git:rev-parse', 'git:ls-remote', 'curl',
        ])
        for call in calls:
            if call['event'] in {'repair', 'audit', 'save'}:
                self.assertEqual(call['args'][call['args'].index('--model') + 1], 'gpt-6-sol')
            if call['event'] in {'build', 'up', 'migrate', 'pytest', 'script-tests'}:
                self.assertEqual(call['args'][:9], [
                    'compose', '-p', 'tunagumo-dev', '--env-file', '../.env.test',
                    '-f', 'docker-compose.yml', '-f', 'docker-compose.test.yml',
                ])

    def test_shell_regressions_run_in_development_container(self):
        result, calls = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        check = next(c for c in calls if c['event'] == 'script-tests')
        self.assertEqual(check['args'][-8:], ['api', 'python', '-m', 'unittest', 'discover', '-s', '/qa/tests', '-v'])
        mounts = [a for a in check['args'] if ':/qa/' in a]
        self.assertEqual(len(mounts), 3)
        self.assertTrue(all(m.endswith(':ro') for m in mounts))

    def test_manual_branches_stop_before_pull_or_agent(self):
        for branch in ('local/image-check', 'vps/manual-fix'):
            with self.subTest(branch=branch):
                result, calls = self.run_script(branch=branch)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual([c['event'] for c in calls], ['git:symbolic-ref'])

    def test_detached_head_or_branch_lookup_failure_stops(self):
        self.assert_stopped('git:symbolic-ref', ['git:pull', 'build', 'repair', 'save', 'curl'])

    def test_existing_changes_stop_before_pull_or_agent(self):
        result, calls = self.run_script(start_dirty=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([c['event'] for c in calls], ['git:symbolic-ref', 'git:status'])
        self.assertIn('手動作業を保持', result.stderr)

    def test_initial_status_failure_stops_before_pull(self):
        self.assert_stopped('git:status1', ['git:pull', 'build', 'repair', 'save', 'curl'])

    def test_pull_failure_stops_before_build(self):
        self.assert_stopped('git:pull', ['build', 'repair', 'save', 'curl'])

    def test_build_failure_stops_before_repair(self):
        self.assert_stopped('build1', ['up', 'repair', 'save', 'curl'])

    def test_database_start_failure_stops_before_repair(self):
        self.assert_stopped('up', ['repair', 'save', 'curl'])

    def test_migration_failure_stops_before_repair(self):
        self.assert_stopped('migrate1', ['repair', 'save', 'curl'])

    def test_repair_failure_stops_before_audit(self):
        self.assert_stopped('repair', ['audit', 'save', 'curl'])

    def test_audit_failure_stops_before_save(self):
        self.assert_stopped('audit', ['pytest', 'save', 'curl'])

    def test_rebuild_failure_stops_before_save(self):
        self.assert_stopped('build2', ['pytest', 'save', 'curl'])

    def test_final_migration_failure_stops_before_save(self):
        self.assert_stopped('migrate2', ['pytest', 'save', 'curl'])

    def test_pytest_failure_stops_even_if_agent_claims_success(self):
        self.assert_stopped('pytest', ['save', 'curl'])

    def test_zero_collected_tests_stops_before_save(self):
        self.assert_stopped('pytest', ['save', 'curl'], code=5)

    def test_script_regression_failure_stops_before_save(self):
        self.assert_stopped('script-tests', ['save', 'curl'])

    def test_dirty_worktree_is_not_reported_as_saved(self):
        result, calls = self.run_script(dirty=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('curl', [c['event'] for c in calls])

    def test_unpushed_commit_is_not_reported_as_saved(self):
        result, calls = self.run_script(unpushed=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('curl', [c['event'] for c in calls])

    def test_worktree_lookup_failure_does_not_notify_success(self):
        self.assert_stopped('git:status2', ['curl'])

    def test_remote_lookup_failure_does_not_notify_success(self):
        self.assert_stopped('git:ls-remote', ['curl'])

    def test_save_failure_does_not_notify_success(self):
        self.assert_stopped('save', ['curl'])

    def test_failure_retries_with_scheduled_model(self):
        result, calls = self.run_script(fail='repair1')
        self.assertEqual(result.returncode, 0, result.stderr)
        repair = [c for c in calls if c['event'] == 'repair']
        self.assertEqual(len(repair), 2)
        for call in repair:
            self.assertEqual(call['args'][call['args'].index('--model') + 1], 'gpt-6-sol')

    def test_configured_model_applies_to_every_stage_and_retry(self):
        result, calls = self.run_script(fail='repair1', model='test-model')
        self.assertEqual(result.returncode, 0, result.stderr)
        agents = [c for c in calls if c['event'] in {'repair', 'audit', 'save'}]
        self.assertEqual(len(agents), 4)
        for call in agents:
            self.assertEqual(call['args'][call['args'].index('--model') + 1], 'test-model')

    def test_successful_quota_test_does_not_rerun_agent(self):
        result, calls = self.run_script(quota_text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(c['event'] == 'repair' for c in calls), 1)

    def test_backticks_are_literal_prompt_text(self):
        result, calls = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(c['event'] in {'run', 'up'} and c['args'][:1] != ['compose'] for c in calls))
        repair = next(c for c in calls if c['event'] == 'repair')
        self.assertIn('`run --rm`', repair['args'][-1])


if __name__ == '__main__':
    unittest.main()
