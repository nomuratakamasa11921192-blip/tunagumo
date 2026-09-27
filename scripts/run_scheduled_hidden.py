"""Run an existing scheduled command without allocating a Windows console."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workdir', required=True)
    parser.add_argument('--log-file', required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command
    if command and command[0] == '--':
        command = command[1:]
    if not command:
        parser.error('a command is required after --')
    log_path = Path(args.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('a', encoding='utf-8', buffering=1) as log:
        started = datetime.now(timezone.utc).isoformat()
        log.write(f'[{started}] Starting scheduled command\n')
        log.flush()
        try:
            result = subprocess.run(
                command, cwd=args.workdir, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                check=False,
            )
            status = result.returncode
        except OSError as error:
            log.write(f'Launch failed: {type(error).__name__} (errno={error.errno})\n')
            status = 127
        finished = datetime.now(timezone.utc).isoformat()
        log.write(f'[{finished}] Exit code: {status}\n')
    return status


if __name__ == '__main__':
    raise SystemExit(main())
