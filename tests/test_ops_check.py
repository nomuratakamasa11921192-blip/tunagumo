"""Verify scheduled diagnosis without SSH, AI, notifications or backup deletion."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OpsCheckTests(unittest.TestCase):
    def run_check(self, healthy=False, model='', claude_model=''):
        with tempfile.TemporaryDirectory(prefix='tunagumo-ops-test-') as tmp:
            root = Path(tmp)
            (root / 'scripts').mkdir()
            shutil.copy2(ROOT / 'scripts/ops_check.sh', root / 'scripts/ops_check.sh')
            (root / 'scripts/check_vps_health.sh').write_text(
                'echo "[OK] healthy"\nexit 0\n' if healthy else
                'echo "[NG] backup too old"\nexit 1\n')
            fake_bin = root / 'bin'
            fake_bin.mkdir()
            for name in ('ssh', 'scp', 'powershell', 'codex', 'claude'):
                tool = fake_bin / name
                tool.write_text('#!' + sys.executable + '\n' +
                    'import json, os, sys\n'
                    'from pathlib import Path\n'
                    'with open(os.environ["OPS_TEST_LOG"], "a") as f:\n'
                    '    f.write(json.dumps([Path(sys.argv[0]).name, *sys.argv[1:]]) + "\\n")\n')
                tool.chmod(0o755)
            log = root / 'calls.jsonl'
            env = {**os.environ, 'PATH': str(fake_bin) + os.pathsep + os.environ['PATH'],
                   'OPS_TEST_LOG': str(log), 'OFFSITE_BACKUP_DIR': str(root / 'offsite'),
                   'CODEX_SCHEDULED_MODEL': model, 'CLAUDE_SCHEDULED_MODEL': claude_model}
            result = subprocess.run(['bash', str(root / 'scripts/ops_check.sh')],
                                    env=env, capture_output=True, text=True, timeout=15)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            return result, calls

    def test_healthy_check_does_not_call_agent(self):
        result, calls = self.run_check(healthy=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c[0] for c in calls], ['ssh'])

    def test_anomaly_uses_claude_read_only_and_preserves_instructions(self):
        result, calls = self.run_check()
        self.assertEqual(result.returncode, 1)
        agents = [c for c in calls if c[0] in ('claude', 'codex')]
        self.assertEqual([c[0] for c in agents], ['claude'])  # 成功時は予備のCodexを呼ばない
        agent = agents[0]
        self.assertEqual(agent[agent.index('--model') + 1], 'opus')
        allowed = agent[agent.index('--allowedTools') + 1:agent.index('--allowedTools') + 5]
        self.assertEqual(allowed, ['Bash', 'Read', 'Grep', 'Glob'])
        prompt = agent[agent.index('-p') + 1]
        self.assertIn('本番のファイル・設定・コンテナ・DBは一切変更しない', prompt)
        self.assertIn('sudo は使わない', prompt)

    def test_configured_model_applies_to_diagnosis(self):
        result, calls = self.run_check(claude_model='test-claude')
        self.assertEqual(result.returncode, 1)
        agent = next(c for c in calls if c[0] == 'claude')
        self.assertEqual(agent[agent.index('--model') + 1], 'test-claude')


if __name__ == '__main__':
    unittest.main()
