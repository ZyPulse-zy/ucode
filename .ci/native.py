#!/usr/bin/env python3
"""Independent native FFI test validation; not part of the upstream patch."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import urllib.request

p = argparse.ArgumentParser()
p.add_argument('--candidate', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--reuse-prefix', type=Path)
a = p.parse_args()
src, out = a.candidate.resolve(), a.output.resolve()
out.mkdir(parents=True, exist_ok=True)
prefix = a.reuse_prefix.resolve() if a.reuse_prefix else out / 'prefix'

def run(name, cmd, cwd=None, env=None):
    with (out / (name + '.log')).open('w') as f:
        r = subprocess.run(list(map(str, cmd)), cwd=cwd, env=env,
                           stdout=f, stderr=subprocess.STDOUT)
    if r.returncode:
        raise RuntimeError(f'{name} failed: {r.returncode}')

if not a.reuse_prefix:
    deps = [
      ('libffi', 'https://github.com/libffi/libffi/releases/download/v3.4.7/libffi-3.4.7.tar.gz',
       '138607dee268bdecf374adf9144c00e839e38541f75f24a1fcf18b78fda48b2d', 'libffi-3.4.7'),
      ('json-c', 'https://github.com/json-c/json-c/releases/download/json-c-0.19-20260627/json-c-0.19-nodoc.tar.gz',
       '704927172443309a8efeb162060bb215548e1286e5568514007dd2cc35a0a164', 'json-c-0.19')]
    for name, url, sha, dirname in deps:
        archive = out / (name + '.tar.gz')
        urllib.request.urlretrieve(url, archive)
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == sha
        with tarfile.open(archive) as tf:
            tf.extractall(out / 'sources', filter='data')
        dep = out / 'sources' / dirname
        build = out / ('build-' + name)
        build.mkdir(exist_ok=True)
        if name == 'libffi':
            run(name+'-configure', [dep/'configure', '--prefix='+str(prefix),
                '--disable-docs', '--disable-multi-os-directory'], build)
            run(name+'-build', ['make', '-j2'], build)
            run(name+'-install', ['make', 'install'], build)
        else:
            run(name+'-configure', ['cmake', '-S', dep, '-B', build,
                '-DCMAKE_INSTALL_PREFIX='+str(prefix), '-DBUILD_TESTING=OFF',
                '-DBUILD_SHARED_LIBS=ON', '-DBUILD_STATIC_LIBS=OFF'])
            run(name+'-build', ['cmake', '--build', build, '-j2'])
            run(name+'-install', ['cmake', '--install', build])

env = dict(os.environ)
env['LD_LIBRARY_PATH'] = ':'.join(str(prefix / x) for x in ['lib', 'lib64'])
env['PKG_CONFIG_PATH'] = ':'.join(str(prefix / x / 'pkgconfig') for x in ['lib', 'lib64'])
env['LDFLAGS'] = f'-L{prefix}/lib -L{prefix}/lib64 -Wl,-rpath,{prefix}/lib'
build = out / 'ucode-build'
run('ucode-configure', ['cmake', '-S', src, '-B', build,
    '-DCMAKE_PREFIX_PATH='+str(prefix), '-DFFI_SUPPORT=ON',
    *['-D'+m+'_SUPPORT=OFF' for m in ['DEBUG','LOG','UBUS','UCI','ULOOP','NL80211','RTNL','ZLIB','DIGEST']]], env=env)
run('ucode-build', ['cmake','--build',build,'-j2'], env=env)
assert 'HAVE_FFI_COMPLEX_TYPES:INTERNAL=1' in (build/'CMakeCache.txt').read_text()
run('linked-libraries', ['ldd',build/'ffi.so'], env=env)
assert str(prefix) in (out/'linked-libraries.log').read_text()
tests = sorted((src/'tests/custom/17_lib_ffi').glob('[0-9][0-9]_*'))
assert len(tests) == 35
assert sum(t.read_text().count('-- Testcase --') for t in tests) == 109
env.update(UCODE_BIN=str(build/'ucode'), UCODE_LIB=str(build))
run('ffi-suite', [build/'ucode','-L',str(build/'*.so'),'-S',src/'tests/custom/run_tests.uc',*tests],
    cwd=src/'tests/custom', env=env)
log = (out/'ffi-suite.log').read_text()
assert 'Ran 35 tests, 35 okay, 0 failures' in log
assert 'skipped' not in log
report = {'candidate':subprocess.check_output(['git','-C',str(src),'rev-parse','HEAD'],text=True).strip(),
          'libffi':'3.4.7','json-c':'0.19','files':35,'cases':109,'skips':0,'result':'passed',
          'native_full_ffi_suite':True,'cross_or_hardware_claim':False}
(out/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
