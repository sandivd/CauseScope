"""Owned Windows Job Object lifecycle; no privileges, breakaway or shell.

Create the target suspended, assign it to our nested job, then resume its primary
thread. Assignment after a running Popen would race with descendant creation.
Only stdin/stdout/stderr handles are inherited. Import is safe on other platforms;
the Windows DLL is loaded only when this backend is used.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
from functools import lru_cache
import math
import os
import subprocess
import sys
import time


SIZE_T = ctypes.c_size_t
WAIT_OBJECT_0, WAIT_TIMEOUT, INFINITE = 0, 258, 0xFFFFFFFF
KILL_ON_JOB_CLOSE = 0x2000


class _BasicLimits(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                ('flags', w.DWORD), ('min_working_set', SIZE_T), ('max_working_set', SIZE_T),
                ('active_limit', w.DWORD), ('affinity', SIZE_T),
                ('priority', w.DWORD), ('scheduling', w.DWORD)]


class _IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ('read_operations', 'write_operations', 'other_operations',
                 'read_bytes', 'write_bytes', 'other_bytes')]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [('basic', _BasicLimits), ('io', _IOCounters),
                ('process_memory', SIZE_T), ('job_memory', SIZE_T),
                ('peak_process_memory', SIZE_T), ('peak_job_memory', SIZE_T)]


class _Accounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int64) for name in
                ('user_time', 'kernel_time', 'period_user_time', 'period_kernel_time')] + [
                (name, w.DWORD) for name in ('page_faults', 'total_processes', 'active_processes', 'terminated')]


@lru_cache(maxsize=1)
def _kernel():
    import _winapi
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    definitions = {
        'CreateJobObjectW': (w.HANDLE, [ctypes.c_void_p, w.LPCWSTR]),
        'SetInformationJobObject': (w.BOOL, [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]),
        'QueryInformationJobObject': (w.BOOL, [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p]),
        'AssignProcessToJobObject': (w.BOOL, [w.HANDLE, w.HANDLE]),
        'TerminateJobObject': (w.BOOL, [w.HANDLE, w.UINT]),
        'GetCurrentProcess': (w.HANDLE, []),
        'DuplicateHandle': (w.BOOL, [w.HANDLE, w.HANDLE, w.HANDLE, ctypes.POINTER(w.HANDLE), w.DWORD, w.BOOL, w.DWORD]),
        'ResumeThread': (w.DWORD, [w.HANDLE]),
        'WaitForSingleObject': (w.DWORD, [w.HANDLE, w.DWORD]),
        'GetExitCodeProcess': (w.BOOL, [w.HANDLE, ctypes.POINTER(w.DWORD)]),
        'CloseHandle': (w.BOOL, [w.HANDLE]),
    }
    for name, (restype, argtypes) in definitions.items():
        function = getattr(api, name)
        function.restype, function.argtypes = restype, argtypes
    # Keep CPython's argument/environment checks, isolated-interpreter restriction
    # and native creation/termination audit events, while retaining the thread handle.
    api.CreateProcess = _winapi.CreateProcess
    api.TerminateProcess = _winapi.TerminateProcess
    return api


def _check(value, operation):
    if not value:
        error = ctypes.WinError(ctypes.get_last_error())
        error.strerror = operation + ': ' + error.strerror
        raise error
    return value


class WindowsJobProcess:
    """The small Popen interface required by run_once, with an owned job handle."""

    def __init__(self, command, *, cwd, env, stdout, stderr):
        import msvcrt
        self.args, self.pid, self.returncode = command, None, None
        self._api = api = _kernel()
        self._job = self._process = None
        command_line = subprocess.list2cmdline(command)
        cwd = os.fspath(cwd)
        if any('\0' in value for value in [command_line, cwd, *env, *env.values()]):
            raise ValueError('Embedded null in process arguments or environment')
        startup = subprocess.STARTUPINFO()
        startup.dwFlags = subprocess.STARTF_USESTDHANDLES
        duplicates, thread = [], None
        try:
            self._job = _check(api.CreateJobObjectW(None, None), 'CreateJobObjectW')
            limits = _ExtendedLimits()
            limits.basic.flags = KILL_ON_JOB_CLOSE
            _check(api.SetInformationJobObject(self._job, 9, ctypes.byref(limits), ctypes.sizeof(limits)),
                   'SetInformationJobObject')
            with open(os.devnull, 'rb') as stdin:
                current = api.GetCurrentProcess()
                for stream in (stdin, stdout, stderr):
                    handle = w.HANDLE()
                    _check(api.DuplicateHandle(current, msvcrt.get_osfhandle(stream.fileno()),
                                              current, ctypes.byref(handle), 0, True, 2), 'DuplicateHandle')
                    duplicates.append(handle.value)
            startup.hStdInput, startup.hStdOutput, startup.hStdError = duplicates
            startup.lpAttributeList = {'handle_list': duplicates}
            # Retain Python's audit event; no shell, alternate token or breakaway.
            sys.audit('subprocess.Popen', None, command_line, cwd, env)
            flags = 0x4 | subprocess.CREATE_NEW_PROCESS_GROUP
            try:
                self._process, thread, self.pid, _ = api.CreateProcess(
                    None, command_line, None, None, True, flags, env, cwd, startup)
            except OSError as exc:
                exc.strerror = 'CreateProcess: ' + str(exc.strerror)
                raise
            _check(api.AssignProcessToJobObject(self._job, self._process), 'AssignProcessToJobObject')
            previous = api.ResumeThread(thread)
            if previous == INFINITE:
                _check(False, 'ResumeThread')
            if previous != 1:
                raise OSError('Unexpected initial thread suspension count')
        except BaseException:
            # On assignment/resume failure, never leave an uncontained target running.
            if self._process:
                try:
                    self.kill()
                    self.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            self.close()
            raise
        finally:
            if thread:
                api.CloseHandle(thread)
            for handle in duplicates:
                api.CloseHandle(handle)

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        result = self._api.WaitForSingleObject(self._process, 0)
        if result == WAIT_TIMEOUT:
            return None
        if result != WAIT_OBJECT_0:
            _check(False, 'WaitForSingleObject')
        code = w.DWORD()
        _check(self._api.GetExitCodeProcess(self._process, ctypes.byref(code)), 'GetExitCodeProcess')
        self.returncode = code.value
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is not None:
            return self.returncode
        milliseconds = INFINITE if timeout is None else min(INFINITE - 1, max(0, math.ceil(timeout * 1000)))
        result = self._api.WaitForSingleObject(self._process, milliseconds)
        if result == WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(self.args, timeout)
        if result != WAIT_OBJECT_0:
            _check(False, 'WaitForSingleObject')
        return self.poll()

    def kill(self):
        try:
            self._api.TerminateProcess(self._process, 1)
        except PermissionError:
            if self.poll() is None:
                raise

    def terminate_tree(self):
        _check(self._api.TerminateJobObject(self._job, 1), 'TerminateJobObject')
        deadline = time.monotonic() + 5  # Same OS cleanup allowance as the former taskkill backend.
        while True:
            accounting = _Accounting()
            _check(self._api.QueryInformationJobObject(self._job, 1, ctypes.byref(accounting),
                                                      ctypes.sizeof(accounting), None), 'QueryInformationJobObject')
            if not accounting.active_processes:
                break
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired('Windows job termination', 5)
            time.sleep(0.01)
        if self._process:
            self.wait(timeout=max(0, deadline - time.monotonic()))

    def close(self):
        error = None
        if self._job:
            try:
                self.terminate_tree()
            except (OSError, subprocess.TimeoutExpired) as exc:
                error = f'Process-tree termination failed: {exc}'
            finally:
                if not self._api.CloseHandle(self._job):
                    detail = f'Job handle cleanup failed: {ctypes.WinError(ctypes.get_last_error())}'
                    error = detail if error is None else error + '; ' + detail
                self._job = None
        if self._process:
            if not self._api.CloseHandle(self._process):
                detail = f'Process handle cleanup failed: {ctypes.WinError(ctypes.get_last_error())}'
                error = detail if error is None else error + '; ' + detail
            self._process = None
        return error
