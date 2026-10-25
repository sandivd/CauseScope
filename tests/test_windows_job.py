"""Owned-job startup order, failure safety and real Windows descendant cleanup."""
import ctypes
import os
import subprocess
import json
import sys
import time

import pytest

from causescope import runner
from causescope import windows_job


@pytest.fixture
def native_api(monkeypatch):
    if os.name != 'nt':
        pytest.skip('Native Windows API validation; shared runner tests cover POSIX')
    real = windows_job._kernel()
    calls = []
    overrides = {}
    class Proxy:
        def __getattr__(self, name):
            original = getattr(real, name)
            def call(*args):
                calls.append(name)
                if name in overrides:
                    return overrides[name](*args)
                return original(*args)
            return call
    monkeypatch.setattr(windows_job, '_kernel', lambda: Proxy())
    return real, calls, overrides


def test_target_is_suspended_before_job_assignment_and_resume(native_api, make_spec):
    real, calls, overrides = native_api
    def create(*args):
        assert args[5] & 4  # CREATE_SUSPENDED; no target code can run yet.
        assert not args[5] & 0x1000000  # Never CREATE_BREAKAWAY_FROM_JOB.
        assert args[8].lpAttributeList['handle_list']
        return real.CreateProcess(*args)
    overrides['CreateProcess'] = create
    result = runner.run_once(make_spec(command=['{python}', '-c', 'print("ready")'], failure={'exit_code':99}), {})
    assert result.outcome == 'pass' and result.stdout.strip() == 'ready'
    assert calls.index('CreateProcess') < calls.index('AssignProcessToJobObject') < calls.index('ResumeThread')
    assert calls.count('DuplicateHandle') == 3
    assert result.termination_error is None and result.cleanup_error is None


@pytest.mark.parametrize('operation', ['SetInformationJobObject', 'DuplicateHandle',
                                       'CreateProcess', 'AssignProcessToJobObject', 'ResumeThread'])
def test_failed_setup_never_executes_target_or_falls_back_uncontained(native_api, make_spec, tmp_path, operation):
    real, calls, overrides = native_api
    marker = tmp_path / 'target-ran'
    def denied(*args):
        if operation == 'CreateProcess':
            raise ctypes.WinError(5)
        ctypes.set_last_error(5)
        return windows_job.INFINITE if operation == 'ResumeThread' else 0
    overrides[operation] = denied
    result = runner.run_once(make_spec(command=['{python}', '-c',
        'from pathlib import Path; import sys; Path(sys.argv[1]).touch()',str(marker)], failure={'exit_code':99}), {})
    assert result.outcome == 'inconclusive' and result.exit_code is None
    assert operation in result.launch_error and 'WinError 5' in result.launch_error
    assert not marker.exists()
    if operation in ('AssignProcessToJobObject','ResumeThread'):
        assert 'TerminateProcess' in calls
    if operation == 'AssignProcessToJobObject':
        assert 'ResumeThread' not in calls


def test_job_termination_failure_remains_inconclusive(native_api, make_spec):
    real, calls, overrides = native_api
    def denied(*args):
        ctypes.set_last_error(5)
        return False
    overrides['TerminateJobObject'] = denied
    result = runner.run_once(make_spec(command=['{python}','-c','import time; time.sleep(30)'],
                                      failure={'timeout':True},settings={'timeout_seconds':.2}), {})
    assert result.timed_out and result.outcome == 'inconclusive'
    assert 'TerminateJobObject' in result.termination_error
    assert 'TerminateProcess' in calls  # Owned-root fallback is not mistaken for tree success.


def test_job_accounting_failure_remains_inconclusive(native_api, make_spec):
    real, calls, overrides = native_api
    def denied(*args):
        ctypes.set_last_error(5)
        return False
    overrides['QueryInformationJobObject'] = denied
    result = runner.run_once(make_spec(command=['{python}','-c','pass'],failure={'exit_code':99}), {})
    assert result.outcome == 'inconclusive'
    assert 'QueryInformationJobObject' in result.termination_error


def test_handle_close_failure_remains_inconclusive(native_api, make_spec):
    real, calls, overrides = native_api
    # Close the real handle to avoid a test leak, then simulate the API error report.
    def failed_close(handle):
        real.CloseHandle(handle)
        ctypes.set_last_error(5)
        return False
    overrides['CloseHandle'] = failed_close
    result = runner.run_once(make_spec(command=['{python}','-c','pass'],failure={'exit_code':99}), {})
    assert result.outcome == 'inconclusive' and 'handle cleanup failed' in result.termination_error


def test_exit_code_259_is_not_mistaken_for_still_active(native_api, make_spec):
    result = runner.run_once(make_spec(command=['{python}','-c','raise SystemExit(259)'],failure={'exit_code':259}), {})
    assert result.exit_code == 259 and result.outcome == 'failure'


def test_normal_parent_exit_also_cleans_descendant(native_api, make_spec, tmp_path):
    marker = tmp_path / 'normal-child-survived'
    child = 'import pathlib,sys,time;time.sleep(.6);pathlib.Path(sys.argv[1]).touch()'
    parent = 'import subprocess,sys;subprocess.Popen([sys.executable,"-c",sys.argv[1],sys.argv[2]])'
    result = runner.run_once(make_spec(command=['{python}','-c',parent,child,str(marker)],failure={'exit_code':99}), {})
    assert result.exit_code == 0 and result.outcome == 'pass'
    assert result.termination_error is None and result.cleanup_error is None
    time.sleep(.8)
    assert not marker.exists()


def test_timeout_cleans_grandchild(native_api, make_spec, tmp_path):
    marker = tmp_path / 'grandchild-survived'
    leaf = 'import pathlib,sys,time;time.sleep(1.5);pathlib.Path(sys.argv[1]).touch()'
    branch = 'import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",sys.argv[1],sys.argv[2]]);print("spawned",flush=True);time.sleep(30)'
    parent = 'import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",sys.argv[1],sys.argv[2],sys.argv[3]]);time.sleep(30)'
    result = runner.run_once(make_spec(command=['{python}','-c',parent,branch,leaf,str(marker)],
                                      failure={'timeout':True},settings={'timeout_seconds':.4}), {})
    assert result.outcome == 'failure' and 'spawned' in result.stdout
    assert result.termination_error is None and result.cleanup_error is None
    time.sleep(1.6)
    assert not marker.exists()


def test_keyboard_interrupt_cleans_job_before_propagating(native_api, make_spec, tmp_path, monkeypatch):
    ready, marker = tmp_path/'ready', tmp_path/'interrupted-child-survived'
    child = 'import pathlib,sys,time;time.sleep(1.5);pathlib.Path(sys.argv[1]).touch()'
    parent = ('import pathlib,subprocess,sys,time;'
              'subprocess.Popen([sys.executable,"-c",sys.argv[1],sys.argv[2]]);'
              'pathlib.Path(sys.argv[3]).touch();time.sleep(30)')
    wait = windows_job.WindowsJobProcess.wait
    first = [True]
    def interrupt(process, timeout=None):
        if first[0]:
            first[0] = False
            deadline = time.monotonic()+3
            while not ready.exists() and time.monotonic()<deadline:
                time.sleep(.01)
            assert ready.exists(), 'Child startup handshake did not complete'
            raise KeyboardInterrupt
        return wait(process,timeout)
    monkeypatch.setattr(windows_job.WindowsJobProcess,'wait',interrupt)
    with pytest.raises(KeyboardInterrupt):
        runner.run_once(make_spec(command=['{python}','-c',parent,child,str(marker),str(ready)],failure={'exit_code':99}), {})
    time.sleep(1.6)
    assert not marker.exists()


@pytest.mark.parametrize('event', ['subprocess.Popen', '_winapi.CreateProcess'])
def test_python_audit_denial_still_prevents_target_execution(native_api, tmp_path, event):
    marker = tmp_path / 'audit-blocked-target'
    script = '''
import json,pathlib,sys
from causescope.spec import parse_spec
from causescope.runner import run_once
def deny(event,args):
    if event==sys.argv[1]: raise PermissionError('audit denied process creation')
sys.addaudithook(deny)
spec=parse_spec({'version':1,'name':'audit policy','scope':'local',
    'command':['{python}','-c','import pathlib,sys;pathlib.Path(sys.argv[1]).touch()',sys.argv[2]],
    'parameters':{'A':{'baseline':'0','values':['1']}},'failure':{'exit_code':23}},pathlib.Path('test.yaml'))
record=run_once(spec,spec.baseline)
print(json.dumps({'outcome':record.outcome,'launch_error':record.launch_error}))
'''
    process = subprocess.run([sys.executable,'-c',script,event,str(marker)],
                             capture_output=True,text=True,timeout=15)
    assert process.returncode == 0, process.stderr
    record = json.loads(process.stdout)
    assert record['outcome'] == 'inconclusive' and 'audit denied' in record['launch_error']
    assert not marker.exists()
