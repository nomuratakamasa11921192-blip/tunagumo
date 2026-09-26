"""Run on the approved VPS from a private stage with release.json. No ENV changes.

Uses the existing Docker operator permission. Writes are limited to six reviewed
application files. The live writable layers, host sources and recreation images
are kept consistent. The additive DB column is retained on code rollback.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

os.umask(0o077)
stage = Path(__file__).resolve().parent
root = Path('/opt/tsunagumo/saas')
assert subprocess.check_output(['hostname'], text=True).strip() == 'tk2-119-60133'
assert stage.is_relative_to('/home/ubuntu')
release = json.loads((stage / 'release.json').read_text())
assert release['commit'] == 'cb4d945'
files = release['files']
allowed = {'src/core/models.py', 'src/api/routes/account.py', 'src/channels/mail.py',
           'src/agent/mail_scan.py', 'frontend/index.html', 'migrations/versions/0026_mail_subject_scope.py'}
assert set(files) == allowed
old_names = sorted(allowed - {'migrations/versions/0026_mail_subject_scope.py'})
stamp = time.strftime('%Y%m%d_%H%M%S')
backup = stage / ('backup-' + stamp)
backup.mkdir(mode=0o700)


def run(*args, data=None):
    return subprocess.check_output(args, input=data)


def inspect(name):
    return json.loads(run('docker', 'inspect', name))[0]


def digest(data):
    return hashlib.sha256(data).hexdigest()


reader = 'import base64,json,pathlib,sys; print(json.dumps({n:base64.b64encode((pathlib.Path("/app")/n).read_bytes()).decode() for n in sys.argv[1:]}))'
writer = '''import base64,hashlib,json,os,pathlib,sys
root=pathlib.Path(sys.argv[1]); payload=json.load(sys.stdin)
for name, content in payload.items():
 p=root/name
 assert p.resolve().is_relative_to(root.resolve()) and not p.is_symlink()
 assert p.parent.is_dir()
 data=base64.b64decode(content,validate=True)
 tmp=p.with_name('.customer-release-'+p.name)
 tmp.write_bytes(data); tmp.chmod(0o644); os.replace(tmp,p)
'''
containers = ['docker-api-1', 'docker-scheduler-1']
before = {name: inspect(name) for name in containers}
db_before = inspect('docker-db-1')
old_payloads = {}
for name in containers:
    assert before[name]['State']['Running']
    old_payloads[name] = json.loads(run('docker', 'exec', name, 'python', '-c', reader, *old_names))
    for path, content in old_payloads[name].items():
        expected = files[path]['old']
        if name == 'docker-scheduler-1' and path == 'frontend/index.html':
            expected = '8ffd42d457195eb69425b97eea9704f76fb41e2c6e67d15ce7e93117de807046'
        assert digest(base64.b64decode(content)) == expected, (name, path)
    (backup / (name + '.json')).write_text(json.dumps(old_payloads[name]))
host_payload = {name: base64.b64encode((root / name).read_bytes()).decode() for name in old_names}
for name, content in host_payload.items():
    assert digest(base64.b64decode(content)) == files[name]['old'], name
(backup / 'host.json').write_text(json.dumps(host_payload))
assert not (root / 'migrations/versions/0026_mail_subject_scope.py').exists()

def sql(query):
    return run('docker', 'exec', 'docker-db-1', 'psql', '-U', 'tsunagumo', '-d', 'tsunagumo', '-Atc', query).decode().strip()

assert sql('SELECT version_num FROM alembic_version') == '0025'
assert sql("SELECT count(*) FROM sessions WHERE status IN ('RUNNING','PENDING')") == '0'
assert sql('SELECT count(*) FROM tenant_mail_accounts WHERE enabled=true') == '0'
with (backup / 'database.dump').open('wb') as out:
    subprocess.run(['docker', 'exec', 'docker-db-1', 'pg_dump', '-U', 'tsunagumo', '-Fc', 'tsunagumo'], stdout=out, check=True)
assert (backup / 'database.dump').stat().st_size > 1000
with (backup / 'database.dump').open('rb') as source:
    subprocess.run(['docker', 'exec', '-i', 'docker-db-1', 'pg_restore', '--list'], stdin=source, stdout=subprocess.DEVNULL, check=True)

new_payload = {}
context = stage / ('build-' + stamp)
context.mkdir(mode=0o700)
for name, record in files.items():
    data = base64.b64decode(record['data'], validate=True)
    assert digest(data) == record['sha256'], name
    target = context / 'payload' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    new_payload[name] = record['data']

tags = {}
for name in containers:
    role = 'api' if name == 'docker-api-1' else 'scheduler'
    tag = 'docker-' + role + ':latest'
    old_tag = json.loads(run('docker', 'image', 'inspect', tag))[0]['Id']
    rollback = 'tsunagumo-customer-rollback-' + role + ':' + stamp
    new_tag = 'tsunagumo-customer-' + role + ':' + stamp
    run('docker', 'tag', old_tag, rollback)
    base_tag = 'tsunagumo-customer-base-' + role + ':' + stamp
    run('docker', 'tag', before[name]['Image'], base_tag)
    (context / 'Dockerfile').write_text('FROM ' + base_tag + '\nCOPY --chown=appuser:appuser payload/ /app/\n')
    with (backup / (role + '-build.log')).open('wb') as out:
        subprocess.run(['docker', 'build', '--pull=false', '--network=none', '-t', new_tag, str(context)], stdout=out, stderr=subprocess.STDOUT, check=True)
    image_files = json.loads(run('docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
        '--entrypoint', 'python', new_tag, '-c', reader, *sorted(allowed)))
    assert image_files == new_payload
    tags[name] = {'current': tag, 'rollback': rollback, 'release': new_tag}
(backup / 'image-tags.json').write_text(json.dumps(tags, indent=2))


def write_host(payload):
    args = ['docker', 'run', '--rm', '-i', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--user', '0']
    # Deliberately exclude ENV, workspace, host root and Docker socket.
    for directory in ['src', 'frontend', 'migrations']:
        args += ['--mount', f'type=bind,src={root / directory},dst=/target/{directory}']
    args += ['--entrypoint', 'python', before['docker-api-1']['Image'], '-c', writer, '/target']
    run(*args, data=json.dumps(payload).encode())


def write_live(name, payload):
    run('docker', 'exec', '-i', name, 'python', '-c', writer, '/app', data=json.dumps(payload).encode())


mutated = False
try:
    mutated = True
    write_host(new_payload)
    for name in containers:
        write_live(name, new_payload)
    with (backup / 'migration.log').open('wb') as out:
        subprocess.run(['docker', 'exec', 'docker-api-1', 'alembic', 'upgrade', 'head'], stdout=out, stderr=subprocess.STDOUT, check=True)
    assert sql('SELECT version_num FROM alembic_version') == '0026'
    for name in containers:
        run('docker', 'tag', tags[name]['release'], tags[name]['current'])
        run('docker', 'restart', '-t', '30', name)
    expected_html = files['frontend/index.html']['sha256']
    for attempt in range(20):
        try:
            with urllib.request.urlopen('https://app.tunagumo.com/', timeout=10) as response:
                assert response.status == 200 and digest(response.read()) == expected_html
            break
        except Exception:
            if attempt == 19:
                raise
            time.sleep(2)
    for name in containers:
        after = inspect(name)
        assert after['State']['Running'] and after['Id'] == before[name]['Id']
        assert after['Config']['Env'] == before[name]['Config']['Env']
        assert after['State']['StartedAt'] != before[name]['State']['StartedAt']
        live_files = json.loads(run('docker', 'exec', name, 'python', '-c', reader, *sorted(allowed)))
        assert live_files == new_payload
    assert inspect('docker-db-1')['State']['StartedAt'] == db_before['State']['StartedAt']
    for name in files:
        assert digest((root / name).read_bytes()) == files[name]['sha256']
except Exception:
    if mutated:
        write_host(host_payload)
        for name in containers:
            if not inspect(name)['State']['Running']:
                run('docker', 'start', name)
            write_live(name, old_payloads[name])
            run('docker', 'tag', tags[name]['rollback'], tags[name]['current'])
            run('docker', 'restart', '-t', '30', name)
    print('FAILED: code rollback attempted; additive schema retained; backup=' + str(backup), flush=True)
    raise
print(json.dumps({'deployed': release['commit'], 'backup': str(backup), 'images': tags,
                  'schema': '0026', 'env_unchanged': True, 'db_not_restarted': True}), flush=True)
