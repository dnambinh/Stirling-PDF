"""Real Git fixtures and Docker doubles verify deployment failure boundaries."""
import fcntl
import json
import os
import pathlib
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / 'deploy-container.sh'

class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = pathlib.Path(self.tmp.name)
        self.app = self.base/'app'
        self.app.mkdir()
        self.bin = self.base/'bin'
        self.bin.mkdir()
        self.remote = self.base/'origin'
        self.git('init','-b','production/handlepdf',str(self.remote))
        d=self.remote/'deploy/fev'
        d.mkdir(parents=True)
        for name in ['runtime-images.json','compose.checks.yaml']:
            (d/name).write_bytes((SCRIPT.parent/name).read_bytes())
        self.git('-C',str(self.remote),'add','.')
        self.git('-C',str(self.remote),'-c','user.name=Test','-c','user.email=test@example.com','-c','commit.gpgsign=false','commit','-m','fixture')
        self.git('clone',str(self.remote),str(self.app/'source'))
        self.sha=self.git('-C',str(self.remote),'rev-parse','HEAD').strip()
        for name in ['compose.yaml','core.env','ocr.env']:(self.app/name).write_text('fixture\n')
        (self.app/'compose.deploy.yaml').write_text('previous override\n')
        (self.app/'DEPLOYED_COMMIT').write_text('previous\n')
        (self.app/'ocr/models/paddlex').mkdir(parents=True)
        (self.app/'ocr/models/paddlex/model.txt').write_text('public model fixture')
        self.write('docker', '''#!/usr/bin/env python3
import os,sys,json,pathlib
args=sys.argv[1:]
with open(os.environ['DOCKER_LOG'],'a') as f:f.write(json.dumps(args)+'\\n')
if args[:2]==['buildx','build'] and os.environ.get('FAIL')=='build':sys.exit(1)
if args and (args[0]=='inspect' or args[:2]==['image','inspect']):
 if '--format' not in args:sys.exit(1)
 fmt=args[args.index('--format')+1]
 if fmt=='{{.Id}}':print('wrong' if os.environ.get('FAIL')=='runtime' else next(value for ref,value in json.loads(os.environ['RUNTIME_MAP']).items() if ref in args))
 elif 'com.fev.deploy-run' in fmt:print(args[-1].removeprefix('handlepdf-unit-'))
 elif fmt=='{{.State.Health.Status}}':
  once=pathlib.Path(os.environ['ONCE']);bad=os.environ.get('FAIL')=='ocr-once' and not once.exists()
  print('unhealthy' if bad else 'healthy')
  if bad:once.touch()
 elif 'revision' in fmt:print(os.environ['SHA'])
 else:print('fev/handlepdf:previous')
elif args and args[0]=='compose':
 check='-p' in args
 if 'ps' in args:print('fixture-container')
 if 'up' in args and not check and os.environ.get('FAIL')=='health':
  once=pathlib.Path(os.environ['ONCE'])
  if not once.exists():once.touch();sys.exit(1)
''')
        self.write('curl','#!/bin/sh\nexit 0\n')
        self.write('backup','#!/bin/sh\n[ "$FAIL" != backup ]\n')
        self.write('smoke','#!/bin/sh\n[ "$FAIL" != smoke ]\n')
        self.env=dict(os.environ,PATH=str(self.bin)+':'+os.environ['PATH'],APP_ROOT=str(self.app),CONFIG_ROOT=str(self.base),REPO_URL=str(self.remote),BACKUP_COMMAND=str(self.bin/'backup'),SMOKE_COMMAND=str(self.bin/'smoke'),MAINTENANCE_FILE=str(self.base/'maintenance'),DOCKER_LOG=str(self.base/'docker.log'),ONCE=str(self.base/'once'),SHA=self.sha,RUNTIME_MAP=(SCRIPT.parent/'runtime-images.json').read_text())

    def git(self,*args):
        return subprocess.check_output(['git',*args],stderr=subprocess.DEVNULL,text=True)
    def write(self,name,text):
        p=self.bin/name;p.write_text(text);p.chmod(0o755)
    def deploy(self,failure=''):
        return subprocess.run(['bash',str(SCRIPT)],env=dict(self.env,FAIL=failure),capture_output=True,text=True)
    def log(self):
        p=self.base/'docker.log';return p.read_text() if p.exists() else ''
    def assert_untouched(self,r):
        self.assertNotEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertNotIn('"stop", "--timeout", "900"',self.log())
        self.assertFalse((self.base/'maintenance').exists())
        self.assertEqual(list(self.app.glob('.deploy-*')),[])
    def test_dirty_source_refused(self):
        (self.app/'source/local-edit').write_text('user work')
        self.assert_untouched(self.deploy())
        self.assertEqual(self.log(),'')
    def test_concurrent_deployment_refused(self):
        with (self.app/'deploy.lock').open('w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assert_untouched(self.deploy())
    def test_runtime_drift_refused(self):self.assert_untouched(self.deploy('runtime'))
    def test_build_failure_keeps_production_running(self):self.assert_untouched(self.deploy('build'))
    def test_real_smoke_failure_keeps_production_running(self):self.assert_untouched(self.deploy('smoke'))
    def assert_rollback(self,failure):
        r=self.deploy(failure)
        self.assertNotEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIn('Previous images restored',r.stderr)
        self.assertEqual((self.app/'compose.deploy.yaml').read_text(),'previous override\n')
        self.assertEqual((self.app/'DEPLOYED_COMMIT').read_text(),'previous\n')
        self.assertFalse((self.base/'maintenance').exists())
        self.assertEqual(list(self.app.glob('.deploy-*')),[])
    def test_backup_failure_restores_release(self):self.assert_rollback('backup')
    def test_failed_replacement_restores_release(self):self.assert_rollback('health')
    def test_unhealthy_ocr_is_not_skipped(self):
        r=self.deploy();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        (self.base/'docker.log').write_text('')
        r=self.deploy('ocr-once');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIn('"buildx", "build"',self.log())
    def test_success_and_noop(self):
        r=self.deploy();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual((self.app/'DEPLOYED_COMMIT').read_text().strip(),self.sha)
        self.assertIn('fev/handlepdf-core:git-'+self.sha,(self.app/'compose.deploy.yaml').read_text())
        self.assertIn('fev/handlepdf-ocr:git-'+self.sha,(self.app/'compose.deploy.yaml').read_text())
        (self.base/'docker.log').write_text('')
        r=self.deploy();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIn('Already running',r.stdout)
        self.assertNotIn('"buildx", "build"',self.log())

if __name__=='__main__':unittest.main()
