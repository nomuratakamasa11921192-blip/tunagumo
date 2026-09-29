"""本人が明示的に指示した場合だけ使う、saas配下の少数ファイルの本番反映(ENV・DB・migration変更なし)。
deploy_roomtour_align_20260928.pyを汎用化したもの。対象ファイルと新旧ハッシュはrelease.jsonで指定し、
ホスト・稼働中のapi/schedulerが旧ハッシュと一致する場合だけ反映する。失敗時は旧ファイルとイメージへ自動で戻す。
作成: python scripts/make_release.py <旧commit> <新commit> <saas相対パス...>
実行(VPS, ubuntu): python3 deploy_files.py  (同じフォルダにrelease.json)
"""
import base64, hashlib, json, os, pathlib, subprocess, time, urllib.request
os.umask(0o077)
stage = pathlib.Path(__file__).resolve().parent
root = pathlib.Path('/opt/tsunagumo/saas')
CONTAINERS = ['docker-api-1', 'docker-scheduler-1']


def run(*a, data=None): return subprocess.check_output(a, input=data)
def inspect(n): return json.loads(run('docker', 'inspect', n))[0]
def digest(b): return hashlib.sha256(b).hexdigest()
def sql(q): return run('docker', 'exec', 'docker-db-1', 'psql', '-U', 'tsunagumo', '-d', 'tsunagumo', '-Atc', q).decode().strip()
def live_hashes(n):
    out = run('docker', 'exec', n, 'sha256sum', *('/app/' + f for f in FILES)).decode().split('\n')
    return {line.split()[1][len('/app/'):]: line.split()[0] for line in out if line.strip()}


writer = '''import base64,json,os,pathlib,sys
r=pathlib.Path(sys.argv[1]); payload=json.load(sys.stdin)
for n,c in payload.items():
 p=r/n
 assert p.resolve().is_relative_to(r.resolve()) and not p.is_symlink() and p.parent.is_dir()
 t=p.with_name('.deploy-files-'+p.name)
 t.write_bytes(base64.b64decode(c,validate=True)); t.chmod(0o644); os.replace(t,p)
'''

assert run('hostname').decode().strip() == 'tk2-119-60133'
assert stage.is_relative_to('/home/ubuntu')
release = json.loads((stage / 'release.json').read_text())
FILES = sorted(release['files'])
assert FILES and all((f.startswith('src/') and f.endswith('.py')) or f in ('frontend/index.html', 'frontend/widget.js') for f in FILES)
old_hashes, new_hashes = release['old_sha256'], {n: v['sha256'] for n, v in release['files'].items()}

# 事前確認: ホストと稼働中の両コンテナが想定した旧版(0fb8c52)と一致し、処理中の依頼がない
for n in FILES: assert digest((root / n).read_bytes()) == old_hashes[n], ('host', n)
for c in CONTAINERS: assert live_hashes(c) == old_hashes, ('live', c)
assert sql("SELECT count(*) FROM sessions WHERE status IN ('RUNNING','PENDING')") == '0'
schema = sql('SELECT version_num FROM alembic_version')
before = {c: inspect(c) for c in CONTAINERS}
db_started = inspect('docker-db-1')['State']['StartedAt']

stamp = time.strftime('%Y%m%d_%H%M%S')
backup = stage / ('backup-' + stamp); backup.mkdir(mode=0o700)
host_old = {n: base64.b64encode((root / n).read_bytes()).decode() for n in FILES}
(backup / 'host.json').write_text(json.dumps(host_old))

payload = {}
context = stage / ('build-' + stamp)
for n, v in release['files'].items():
    b = base64.b64decode(v['data'], validate=True); assert digest(b) == v['sha256'], n
    if n.endswith('.py'): compile(b, n, 'exec')
    p = context / 'payload' / n; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b)
    payload[n] = v['data']

tags = {}
for c in CONTAINERS:
    role = 'api' if c == 'docker-api-1' else 'scheduler'
    current = 'docker-' + role + ':latest'
    rollback = 'tsunagumo-deploy-rollback-' + role + ':' + stamp
    new = 'tsunagumo-deploy-' + role + ':' + stamp
    run('docker', 'tag', inspect(current)['Id'], rollback)
    (context / 'Dockerfile').write_text('FROM ' + rollback + '\nCOPY --chown=appuser:appuser payload/ /app/\n')
    with (backup / (role + '-build.log')).open('wb') as out:
        subprocess.run(['docker', 'build', '--pull=false', '--network=none', '-t', new, str(context)],
                       stdout=out, stderr=subprocess.STDOUT, check=True)
    got = run('docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'sha256sum', new,
              *('/app/' + f for f in FILES)).decode().split()
    assert {got[i + 1][len('/app/'):]: got[i] for i in range(0, len(got), 2)} == new_hashes, (c, 'image')
    tags[c] = {'current': current, 'rollback': rollback, 'release': new}
(backup / 'image-tags.json').write_text(json.dumps(tags, indent=2))


def write_host(p):
    args = ['docker', 'run', '--rm', '-i', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--user', '0',
            '--mount', f'type=bind,src={root / "src"},dst=/target/src',
            '--mount', f'type=bind,src={root / "frontend"},dst=/target/frontend',
            '--entrypoint', 'python', before['docker-api-1']['Image'], '-c', writer, '/target']
    run(*args, data=json.dumps(p).encode())


def write_live(c, p): run('docker', 'exec', '-i', c, 'python', '-c', writer, '/app', data=json.dumps(p).encode())


mutated = False
try:
    assert sql("SELECT count(*) FROM sessions WHERE status IN ('RUNNING','PENDING')") == '0'
    mutated = True
    write_host(payload)
    for c in CONTAINERS: write_live(c, payload)
    for c in CONTAINERS:
        run('docker', 'tag', tags[c]['release'], tags[c]['current'])
        run('docker', 'restart', '-t', '30', c)
    for attempt in range(20):
        try:
            with urllib.request.urlopen('https://app.tunagumo.com/', timeout=10) as resp:
                assert resp.status == 200
            break
        except Exception:
            if attempt == 19: raise
            time.sleep(2)
    time.sleep(15)
    for c in CONTAINERS:
        after = inspect(c)
        assert after['State']['Running'] and after['RestartCount'] == before[c]['RestartCount']
        assert after['Config']['Env'] == before[c]['Config']['Env']
        assert live_hashes(c) == new_hashes, ('live after', c)
        logs = subprocess.run(['docker', 'logs', '--since', '1m', c], capture_output=True, text=True)
        assert 'Traceback' not in logs.stdout + logs.stderr, ('traceback', c)
    for n in FILES: assert digest((root / n).read_bytes()) == new_hashes[n]
    assert inspect('docker-db-1')['State']['StartedAt'] == db_started
    assert sql('SELECT version_num FROM alembic_version') == schema
except Exception:
    if mutated:
        write_host(host_old)
        for c in CONTAINERS:
            if not inspect(c)['State']['Running']: run('docker', 'start', c)
            write_live(c, host_old)
            run('docker', 'tag', tags[c]['rollback'], tags[c]['current'])
            run('docker', 'restart', '-t', '30', c)
    print('FAILED: rollback attempted; backup=' + str(backup), flush=True)
    raise

result = {'deployed': release['commit'], 'backup': str(backup), 'images': tags, 'schema': schema,
          'env_unchanged': True, 'db_not_restarted': True}
(backup / 'result.json').write_text(json.dumps(result, indent=2))
print(json.dumps(result), flush=True)
