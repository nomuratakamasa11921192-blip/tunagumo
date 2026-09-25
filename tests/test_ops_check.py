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
    def run_check(self, healthy=False, model=''):
        with tempfile.TemporaryDirectory(prefix='tunagumo-ops-test-') as tmp:
            root = Path(tmp)
            (root / 'scripts').mkdir()
            shutil.copy2(ROOT / 'scripts/ops_check.sh', root / 'scripts/ops_check.sh')
            (root / 'scripts/check_vps_health.sh').write_text(
                'echo "[OK] healthy"\nexit 0\n' if healthy else
                'echo "[NG] backup too old"\nexit 1\n')
            fake_bin = root / 'bin'
            fake_bin.mkdir()
            for name in ('ssh', 'scp', 'powershell', 'codex'):
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
                   'CODEX_SCHEDULED_MODEL': model}
            result = subprocess.run(['bash', str(root / 'scripts/ops_check.sh')],
                                    env=env, capture_output=True, text=True, timeout=15)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            return result, calls

    def test_healthy_check_does_not_call_agent(self):
        result, calls = self.run_check(healthy=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c[0] for c in calls], ['ssh'])

    def test_anomaly_uses_sol_and_preserves_read_only_instructions(self):
        result, calls = self.run_check()
        self.assertEqual(result.returncode, 1)
        agents = [c for c in calls if c[0] == 'codex']
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0][agents[0].index('--model') + 1], 'gpt-6-sol')
        self.assertIn('本番のファイル・設定・コンテナ・DBは一切変更しない', agents[0][-1])
        self.assertIn('sudo は使わない', agents[0][-1])

    def test_configured_model_applies_to_diagnosis(self):
        result, calls = self.run_check(model='test-model')
        self.assertEqual(result.returncode, 1)
        agent = next(c for c in calls if c[0] == 'codex')
        self.assertEqual(agent[agent.index('--model') + 1], 'test-model')


if __name__ == '__main__':
    unittest.main()
