"""Non-privileged diagnostic using only fresh, bounded children owned by this tool."""
import argparse
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import sys


def probe():
    if os.name != 'nt':
        raise OSError('This diagnostic requires Windows')
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'CreateJobObjectW': (w.HANDLE, [ctypes.c_void_p, w.LPCWSTR]),
        'AssignProcessToJobObject': (w.BOOL, [w.HANDLE, w.HANDLE]),
        'TerminateJobObject': (w.BOOL, [w.HANDLE, w.UINT]),
        'CloseHandle': (w.BOOL, [w.HANDLE]),
        'GetCurrentProcess': (w.HANDLE, []),
        'IsProcessInJob': (w.BOOL, [w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL)]),
    }
    for name, (restype, argtypes) in signatures.items():
        function = getattr(api, name)
        function.restype, function.argtypes = restype, argtypes
    result = {'python': sys.version.split()[0], 'windows': list(sys.getwindowsversion()[:3]),
              'privilege_or_acl_changes': False, 'scope': 'Fresh owned diagnostic children only'}
    inside = w.BOOL()
    if not api.IsProcessInJob(api.GetCurrentProcess(), None, ctypes.byref(inside)):
        raise ctypes.WinError(ctypes.get_last_error())
    result['current_process_in_job'] = bool(inside.value)
    command = [sys.executable, '-I', '-c', 'import time;time.sleep(10)']
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        taskkill = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/taskkill.exe'
        process = subprocess.run([str(taskkill), '/PID', str(child.pid), '/T', '/F'],
                                 capture_output=True, timeout=5)
        result['taskkill'] = {'exit_code': process.returncode,
                              'stderr': process.stderr.decode('utf-8', errors='replace').strip(),
                              'child_alive_after': child.poll() is None}
    finally:
        if child.poll() is None:
            child.kill()
        result['owned_child_direct_wait_exit'] = child.wait(timeout=5)
    job = api.CreateJobObjectW(None, None)
    result['create_owned_job'] = {'allowed': bool(job), 'winerror': 0 if job else ctypes.get_last_error()}
    if job:
        child = None
        try:
            child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # This is an API-permission probe, not the runner's race-free launch path.
            ok = api.AssignProcessToJobObject(job, int(child._handle))
            result['assign_owned_child'] = {'allowed': bool(ok), 'winerror': 0 if ok else ctypes.get_last_error()}
            if ok:
                ok = api.TerminateJobObject(job, 1)
                result['terminate_owned_job'] = {'allowed': bool(ok), 'winerror': 0 if ok else ctypes.get_last_error()}
                if ok:
                    result['job_child_exit'] = child.wait(timeout=5)
        finally:
            if child is not None:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)
            api.CloseHandle(job)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-local-execution', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.allow_local_execution:
        parser.error('--allow-local-execution required')
    # Reserve the result before any process starts; historical records cannot be overwritten.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        result = probe()
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
