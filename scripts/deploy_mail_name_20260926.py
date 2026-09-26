"""One-file release for the approved owner-mail integration repair. Run in the VPS stage."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

os.umask(0o077)
STAGE = Path(__file__).resolve().parent
HOST_DIR = Path('/opt/tsunagumo/saas/src/agent')
OLD = '39e95175fcfbc84483a8a52f86ed1807c9318f306286101c19a1a15739523496'
NEW = '577ff39bea33f19cf8dd8ac25bc6c75c1ef37474cdd06ac8f6771b3171152055'
BASES = {'api': 'sha256:43bfdf1e011d19374c8027aed9d0a3919e8b6be9fd9061b7506bac4ae82fa4e8',
         'scheduler': 'sha256:79b73c22845ae86202b2a57bf5b1b16b4af6802f8e2d4349d7704d671da5a28a'}

def run(*args, data=None):
    return subprocess.check_output(args, input=data)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def inspect(name):
    obj = json.loads(run('docker', 'inspect', name))[0]
    return {'id': obj['Id'], 'running': obj['State']['Running'], 'started': obj['State']['StartedAt'],
            'env_hash': sha(json.dumps(obj['Config']['Env']).encode())}

assert run('hostname').decode().strip() == 'tk2-119-60133'
assert STAGE.is_relative_to('/home/ubuntu')
payload = (STAGE / 'mail_scan.py').read_bytes()
assert sha(payload) == NEW
compile(payload, 'mail_scan.py', 'exec')
old = (HOST_DIR / 'mail_scan.py').read_bytes()
assert sha(old) == OLD
before = {role: inspect('docker-' + role + '-1') for role in BASES}
db_before = inspect('docker-db-1')
for role, base in BASES.items():
    assert before[role]['running']
    assert run('docker', 'image', 'inspect', '-f', '{{.Id}}', 'docker-' + role + ':latest').decode().strip() == base
    assert sha(run('docker', 'exec', 'docker-' + role + '-1', 'cat', '/app/src/agent/mail_scan.py')) == OLD
active = run('docker', 'exec', 'docker-db-1', 'psql', '-U', 'tsunagumo', '-d', 'tsunagumo', '-Atc',
             "SELECT count(*) FROM sessions WHERE status IN ('RUNNING','PENDING')").decode().strip()
assert active == '0'
stamp = time.strftime('%Y%m%d_%H%M%S')
backup = STAGE / ('backup-' + stamp)
backup.mkdir(mode=0o700)
(backup / 'mail_scan.py').write_bytes(old)
tags = {}
for role, base in BASES.items():
    rollback = 'tsunagumo-mail-name-rollback-' + role + ':' + stamp
    release = 'tsunagumo-mail-name-' + role + ':' + stamp
    run('docker', 'tag', base, rollback)
    context = STAGE / ('build-' + role + '-' + stamp)
    context.mkdir(mode=0o700)
    (context / 'mail_scan.py').write_bytes(payload)
    (context / 'Dockerfile').write_text('FROM ' + rollback + '\nCOPY --chown=appuser:appuser mail_scan.py /app/src/agent/mail_scan.py\n')
    with (backup / (role + '-build.log')).open('wb') as out:
        subprocess.run(['docker', 'build', '--pull=false', '--network=none', '-t', release, str(context)], stdout=out, stderr=subprocess.STDOUT, check=True)
    assert sha(run('docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                   '--entrypoint', 'cat', release, '/app/src/agent/mail_scan.py')) == NEW
    tags[role] = {'rollback': rollback, 'release': release}
(backup / 'tags.json').write_text(json.dumps(tags, indent=2))
writer = 'import os,pathlib,sys; p=pathlib.Path(sys.argv[1]); t=p.with_name(".mail-name.tmp"); t.write_bytes(sys.stdin.buffer.read()); t.chmod(0o644); os.replace(t,p)'

def write_all(content):
    run('docker', 'run', '--rm', '-i', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges', '--user', '0', '--mount', f'type=bind,src={HOST_DIR},dst=/target',
        '--entrypoint', 'python', BASES['api'], '-c', writer, '/target/mail_scan.py', data=content)
    for role in BASES:
        run('docker', 'exec', '-i', 'docker-' + role + '-1', 'python', '-c', writer, '/app/src/agent/mail_scan.py', data=content)

try:
    write_all(payload)
    for role in BASES:
        run('docker', 'tag', tags[role]['release'], 'docker-' + role + ':latest')
    run('docker', 'restart', 'docker-api-1', 'docker-scheduler-1')
    run('curl', '--fail', '--silent', '--show-error', '--retry', '8', '--retry-delay', '2', '--retry-connrefused',
        '--max-time', '20', 'https://app.tunagumo.com/')
    assert sha((HOST_DIR / 'mail_scan.py').read_bytes()) == NEW
    for role in BASES:
        assert sha(run('docker', 'exec', 'docker-' + role + '-1', 'cat', '/app/src/agent/mail_scan.py')) == NEW
        after = inspect('docker-' + role + '-1')
        assert after['running'] and after['id'] == before[role]['id'] and after['env_hash'] == before[role]['env_hash']
    assert inspect('docker-db-1') == db_before
except Exception:
    write_all(old)
    for role in BASES:
        run('docker', 'tag', tags[role]['rollback'], 'docker-' + role + ':latest')
    run('docker', 'restart', 'docker-api-1', 'docker-scheduler-1')
    print('Rollback applied; verify:', backup)
    raise
print(json.dumps({'deployed': NEW, 'backup': str(backup), 'tags': tags}, indent=2))
