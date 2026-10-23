#!/usr/bin/env python3
"""Restore the exact signed-off local candidate, without changing its identity."""
import argparse, base64, hashlib, json, os, subprocess
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--destination',type=Path,required=True)
p.add_argument('--publish',action='store_true')
a=p.parse_args()
meta=json.loads((Path(__file__).parent/'candidate-bundle.json').read_text())
dest=a.destination.resolve(); dest.mkdir(parents=True,exist_ok=True)
bundle=dest.parent/'candidate.bundle'
bundle.write_bytes(base64.b64decode(meta['bundle_base64'],validate=True))
assert hashlib.sha256(bundle.read_bytes()).hexdigest()==meta['bundle_sha256']
def git(*args):
 return subprocess.check_output(['git','-C',str(dest),*args],text=True).strip()
git('init')
git('remote','add','upstream','https://github.com/ucode-lang/ucode.git')
git('fetch','--depth=8','upstream','079a4de01b38701de88f5b560f68bc4d2c466cfa')
print(git('bundle','verify',str(bundle)))
git('fetch',str(bundle),'HEAD')
git('checkout','--detach',meta['candidate_sha'])
assert git('rev-parse','HEAD')==meta['candidate_sha']
assert git('rev-parse','HEAD^')==meta['parent_sha']
assert git('show','-s','--format=%an <%ae>')=='Yuen Su <zypulsecore@gmail.com>'
assert 'Signed-off-by: Yuen Su <zypulsecore@gmail.com>' in git('show','-s','--format=%B')
assert git('diff','--name-only','HEAD^','HEAD')=='tests/custom/17_lib_ffi/35_complex_numbers'
assert hashlib.sha256((dest/'tests/custom/17_lib_ffi/35_complex_numbers').read_bytes()).hexdigest()=='6bd408820e68ed43f00885284c5198de7357ecb57dc045bd8a48a7f25d5f049a'
git('diff','--check','HEAD^','HEAD')
print(json.dumps({k:v for k,v in meta.items() if k!='bundle_base64'},indent=2))
if a.publish:
 # The checkout action supplies authentication to the separate control checkout.
 # Copy its already-scoped HTTP extraheader through git config, never to logs.
 control=Path(__file__).resolve().parent.parent
 r=subprocess.run(['git','-C',str(control),'config','--get','http.https://github.com/.extraheader'],capture_output=True,text=True,check=True)
 h=r.stdout.strip()
 assert h
 env=dict(os.environ, GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='http.https://github.com/.extraheader', GIT_CONFIG_VALUE_0=h)
 def authenticated_git(*args):
  r=subprocess.run(['git','-C',str(dest),*args],capture_output=True,text=True,env=env)
  if r.returncode:
   raise RuntimeError('Scoped fork git operation failed (credential omitted)')
  return r.stdout.strip()
 branch='refs/heads/fix/ffi-complex-test-capability'
 remote='https://github.com/ZyPulse-zy/ucode.git'
 old=authenticated_git('ls-remote',remote,branch)
 if old:
  assert old.split()[0]==meta['candidate_sha'], 'Ref exists with unexpected head; no overwrite'
 else:
  authenticated_git('push','--force-with-lease='+branch+':',remote,meta['candidate_sha']+':'+branch)
 print('Exact candidate ref published')
