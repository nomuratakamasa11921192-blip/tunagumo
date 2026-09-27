import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'run_scheduled_hidden.py'
spec = importlib.util.spec_from_file_location('scheduled_hidden', SCRIPT)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class HiddenLauncherTests(unittest.TestCase):
    def test_unicode_workdir_output_and_nonzero_exit(self):
        with tempfile.TemporaryDirectory(prefix='qa 日本語 ') as directory:
            log = Path(directory) / 'logs' / 'run.log'
            code = 'import os,sys; print(os.path.basename(os.getcwd())); print("stderr-marker",file=sys.stderr); sys.exit(7)'
            result = launcher.main(['--workdir', directory, '--log-file', str(log), '--', sys.executable, '-X', 'utf8', '-c', code])
            text = log.read_text(encoding='utf-8')
            self.assertEqual(result, 7)
            self.assertIn(Path(directory).name, text)
            self.assertIn('stderr-marker', text)
            self.assertIn('Exit code: 7', text)

    def test_missing_executable_is_failure_and_preserves_log(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'run.log'
            log.write_text('previous run\n', encoding='utf-8')
            result = launcher.main(['--workdir', directory, '--log-file', str(log), '--', str(Path(directory) / 'missing-command')])
            self.assertEqual(result, 127)
            self.assertTrue(log.read_text(encoding='utf-8').startswith('previous run\n'))
            self.assertIn('Launch failed:', log.read_text(encoding='utf-8'))

    def test_success_keeps_separate_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'run.log'
            code = 'import sys; print(repr(sys.argv[1:]))'
            result = launcher.main(['--workdir', directory, '--log-file', str(log), '--', sys.executable, '-c', code, 'space value', 'literal&value'])
            self.assertEqual(result, 0)
            self.assertIn("['space value', 'literal&value']", log.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
