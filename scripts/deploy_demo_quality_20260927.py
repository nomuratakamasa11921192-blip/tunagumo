"""Explicitly approved demo-quality release. No DB/ENV updates."""
import base64, hashlib, json, os, pathlib, subprocess, time, urllib.request
os.umask(0o077)
stage=pathlib.Path(__file__).resolve().parent
root=pathlib.Path('/opt/tsunagumo/saas')
def run(*a,data=None): return subprocess.check_output(a,input=data)
def inspect(n): return json.loads(run('docker','inspect',n))[0]
def digest(b): return hashlib.sha256(b).hexdigest()
assert run('hostname').decode().strip()=='tk2-119-60133'
assert stage.is_relative_to('/home/ubuntu')
release=json.loads((stage/'release.json').read_text())
assert release['commit']=='0fb8c5212752c70ad6fc951752bff2d9eeb58fb9'
allowed={'frontend/index.html','src/agent/graph.py','src/agent/nodes.py','src/api/routes/reel.py','src/api/routes/room_tour.py','src/core/flyer_generator.py','src/video/ffmpeg_ops.py','src/video/reel_editor.py','src/video/room_tour.py','src/video/script.py','src/video/stt.py'}
assert set(release['files'])==allowed
expected=release['expected']
containers=['docker-api-1','docker-scheduler-1']
reader='import base64,json,pathlib,sys; print(json.dumps({n:base64.b64encode((pathlib.Path("/app")/n).read_bytes()).decode() for n in sys.argv[1:]}))'
hasher='import pathlib,hashlib,json; r=pathlib.Path("/app"); print(json.dumps({str(p.relative_to(r)):hashlib.sha256(p.read_bytes()).hexdigest() for d in ["src","frontend","migrations"] for p in (r/d).rglob("*") if p.is_file() and p.suffix in [".py",".html",".css",".js"] and "__pycache__" not in str(p)}))'
writer='''import base64,json,os,pathlib,sys
r=pathlib.Path(sys.argv[1]); payload=json.load(sys.stdin)
for n,c in payload.items():
 p=r/n
 assert p.resolve().is_relative_to(r.resolve()) and not p.is_symlink() and p.parent.is_dir()
 t=p.with_name('.demo-quality-'+p.name)
 t.write_bytes(base64.b64decode(c,validate=True)); t.chmod(0o644); os.replace(t,p)
'''
def sql(q): return run('docker','exec','docker-db-1','psql','-U','tsunagumo','-d','tsunagumo','-Atc',q).decode().strip()
assert sql("SELECT count(*) FROM sessions WHERE status IN ('RUNNING','PENDING')")=='0'
assert sql('SELECT version_num FROM alembic_version')==expected['schema']
before={n:inspect(n) for n in containers}; db_before=inspect('docker-db-1')
for n,x in before.items():
 assert x['State']['Running'] and x['Id']==expected['containers'][n]['id']
 assert json.loads(run('docker','exec',n,'python','-c',hasher))==expected['containers'][n]['live_hashes']
for n,h in expected['host_hashes'].items(): assert digest((root/n).read_bytes())==h,n
stamp=time.strftime('%Y%m%d_%H%M%S')
backup=stage/('backup-'+stamp); backup.mkdir(mode=0o700)
old={n:json.loads(run('docker','exec',n,'python','-c',reader,*sorted(allowed))) for n in containers}
host={n:base64.b64encode((root/n).read_bytes()).decode() for n in allowed}
(backup/'host.json').write_text(json.dumps(host))
for n in containers: (backup/(n+'.json')).write_text(json.dumps(old[n]))
context=stage/('build-'+stamp); context.mkdir(mode=0o700)
payload={}
for n,v in release['files'].items():
 b=base64.b64decode(v['data'],validate=True); assert digest(b)==v['sha256'],n
 if n.endswith('.py'): compile(b,n,'exec')
 p=context/'payload'/n; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(b); payload[n]=v['data']
tags={}
for n in containers:
 role='api' if n=='docker-api-1' else 'scheduler'; current='docker-'+role+':latest'
 old_image=inspect(current)['Id']; assert old_image==expected['containers'][n]['rebuild_image']
 rollback='tsunagumo-demo-rollback-'+role+':'+stamp; new='tsunagumo-demo-'+role+':'+stamp
 run('docker','tag',old_image,rollback)
 (context/'Dockerfile').write_text('FROM '+rollback+'\nCOPY --chown=appuser:appuser payload/ /app/\n')
 with (backup/(role+'-build.log')).open('wb') as out:
  subprocess.run(['docker','build','--pull=false','--network=none','-t',new,str(context)],stdout=out,stderr=subprocess.STDOUT,check=True)
 wanted=dict(expected['containers'][n]['live_hashes']); wanted.update({p:v['sha256'] for p,v in release['files'].items()})
 image_hashes=json.loads(run('docker','run','--rm','--network','none','--read-only','--cap-drop','ALL','--entrypoint','python',new,'-c',hasher))
 assert image_hashes==wanted,(n,'rebuild validation')
 tags[n]={'current':current,'rollback':rollback,'release':new}
(backup/'image-tags.json').write_text(json.dumps(tags,indent=2))
def write_host(p):
 args=['docker','run','--rm','-i','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--user','0']
 for d in ['src','frontend']: args+=['--mount',f'type=bind,src={root/d},dst=/target/{d}']
 args+=['--entrypoint','python',before['docker-api-1']['Image'],'-c',writer,'/target']
 run(*args,data=json.dumps(p).encode())
def write_live(n,p): run('docker','exec','-i',n,'python','-c',writer,'/app',data=json.dumps(p).encode())
mutated=False
try:
 assert sql("SELECT count(*) FROM sessions WHERE status IN ('RUNNING','PENDING')")=='0'
 mutated=True; write_host(payload)
 for n in containers: write_live(n,payload)
 for n in containers:
  run('docker','tag',tags[n]['release'],tags[n]['current']); run('docker','restart','-t','30',n)
 for attempt in range(20):
  try:
   with urllib.request.urlopen('https://app.tunagumo.com/',timeout=10) as resp:
    assert resp.status==200 and digest(resp.read())==release['files']['frontend/index.html']['sha256']
   break
  except Exception:
   if attempt==19: raise
   time.sleep(2)
 for n in containers:
  after=inspect(n)
  assert after['State']['Running'] and after['Id']==before[n]['Id']
  assert after['Config']['Env']==before[n]['Config']['Env']
  assert after['State']['StartedAt']!=before[n]['State']['StartedAt']
  wanted=dict(expected['containers'][n]['live_hashes']); wanted.update({p:v['sha256'] for p,v in release['files'].items()})
  assert json.loads(run('docker','exec',n,'python','-c',hasher))==wanted
 assert inspect('docker-db-1')['State']['StartedAt']==db_before['State']['StartedAt']
 assert sql('SELECT version_num FROM alembic_version')==expected['schema']
 wanted=dict(expected['host_hashes']); wanted.update({p:v['sha256'] for p,v in release['files'].items()})
 for n,h in wanted.items(): assert digest((root/n).read_bytes())==h
except Exception:
 if mutated:
  write_host(host)
  for n in containers:
   if not inspect(n)['State']['Running']: run('docker','start',n)
   write_live(n,old[n]); run('docker','tag',tags[n]['rollback'],tags[n]['current']); run('docker','restart','-t','30',n)
 print('FAILED: source/image rollback attempted; backup='+str(backup),flush=True)
 raise
result={'deployed':release['commit'],'backup':str(backup),'images':tags,'env_unchanged':True,'db_not_restarted':True,'schema':expected['schema']}
(backup/'result.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result),flush=True)
