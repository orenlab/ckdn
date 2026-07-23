# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Win32 process containment. Imported only on Windows.

Windows has no process group that survives re-parenting: ``taskkill /T``
walks parent links, so a grandchild whose parent already exited is missed.
A **job object** holds every descendant regardless of parentage, and one
created with ``KILL_ON_JOB_CLOSE`` takes its members with it when the last
handle goes -- including when ckdn is killed outright and none of its own
cleanup runs.

This lives apart from :mod:`ckdn.runner` for one blunt reason: every helper
here would otherwise have to repeat a ``sys.platform`` guard and both ctypes
imports, purely so that type checkers running on Linux narrow the platform
before they meet a Windows-only name. Ten copies of that preamble is not a
design. A module that is only ever imported on Windows needs none of them.

Everything is best effort. Any call that fails returns a value the caller can
act on, and :func:`terminate_tree` falls back to ``taskkill`` -- worse, but
what ckdn did before jobs existed.
"""

from __future__ import annotations

import contextlib
import ctypes
import functools
import signal
import subprocess
import time
from ctypes import wintypes
from typing import Any

#: Win32 constants for holding and stopping a process tree.
JOB_EXTENDED_LIMIT_INFORMATION = 9
JOB_BASIC_ACCOUNTING_INFORMATION = 1
JOB_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
#: `OpenProcess` failing this way means "it exists, but not for you".
ERROR_ACCESS_DENIED = 5
#: JOBOBJECT_BASIC_ACCOUNTING_INFORMATION is four LARGE_INTEGERs followed by
#: four DWORDs. Every member is naturally aligned, so the struct is 48 bytes
#: and ActiveProcesses -- the third DWORD -- sits at offset 40.
ACCOUNTING_SIZE = 48
ACTIVE_PROCESSES_OFFSET = 40
#: Where a child's job handle is parked, so `terminate_tree` can find it.
JOB_ATTR = "_ckdn_win_job"


@functools.cache
def kernel32() -> Any:
    """The one configured kernel32 binding.

    Cached so the return types below are declared exactly once. Without an
    explicit restype ctypes truncates a handle to a C int, silently corrupting
    it on 64-bit; leaving that to each call site means the next helper added
    here breaks quietly by forgetting an incantation.
    """
    lib = ctypes.WinDLL("kernel32", use_last_error=True)
    lib.OpenProcess.restype = wintypes.HANDLE
    lib.CreateJobObjectW.restype = wintypes.HANDLE
    return lib


def open_process(pid: int, access: int) -> int:
    """A handle for `pid`, or 0. The caller closes it with `close`."""
    lib = kernel32()
    return int(lib.OpenProcess(access, False, wintypes.DWORD(pid)) or 0)


def close(handle: int) -> None:
    """Release a handle. For a job this *is* the kill: KILL_ON_JOB_CLOSE."""
    with contextlib.suppress(OSError):
        kernel32().CloseHandle(wintypes.HANDLE(handle))


def pid_alive(pid: int) -> bool:
    """``os.kill(pid, 0)`` is not usable here.

    A dead pid makes it raise a bare ``OSError`` (``WinError 87``),
    indistinguishable from a real error, and a pid too wide for a C int
    raises ``OverflowError``, which is not an ``OSError`` at all.
    """
    handle = open_process(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if not handle:
        # ACCESS_DENIED means the process exists and simply is not ours to
        # query -- the POSIX branch answers "alive" for exactly that case,
        # and calling it dead would be the one error worth avoiding here.
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        code = wintypes.DWORD()
        if not kernel32().GetExitCodeProcess(
            wintypes.HANDLE(handle), ctypes.byref(code)
        ):
            return False
        # A process that really exits with 259 is misread as alive; that is
        # this API's accepted ambiguity. The cost is a delayed `taskkill`
        # in the jobless branch -- locks stopped depending on pid liveness
        # when they became file locks.
        return bool(code.value == STILL_ACTIVE)
    finally:
        close(handle)


def create_job() -> int | None:
    """A job whose members die when the last handle to it closes."""
    basic = type(
        "_BasicLimits",
        (ctypes.Structure,),
        {
            "_fields_": [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]
        },
    )
    extended = type(
        "_ExtendedLimits",
        (ctypes.Structure,),
        {
            "_fields_": [
                ("BasicLimitInformation", basic),
                # IO_COUNTERS: six ULONGLONGs we never read, present only so
                # the fields after it land at the right offsets.
                ("IoInfo", ctypes.c_ulonglong * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]
        },
    )

    lib = kernel32()
    job = lib.CreateJobObjectW(None, None)
    if not job:
        return None

    info = extended()
    info.BasicLimitInformation.LimitFlags = JOB_LIMIT_KILL_ON_JOB_CLOSE
    if not lib.SetInformationJobObject(
        wintypes.HANDLE(job),
        JOB_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        close(int(job))
        return None
    return int(job)


def assign_job(job: int, pid: int) -> bool:
    """Put the child in the job. A child that already exited cannot be."""
    handle = open_process(pid, PROCESS_SET_QUOTA | PROCESS_TERMINATE)
    if not handle:
        return False
    try:
        return bool(
            kernel32().AssignProcessToJobObject(
                wintypes.HANDLE(job), wintypes.HANDLE(handle)
            )
        )
    finally:
        close(handle)


def job_active(job: int) -> int | None:
    """How many processes are left in the job; None if it cannot be asked."""
    buffer = (ctypes.c_byte * ACCOUNTING_SIZE)()
    if not kernel32().QueryInformationJobObject(
        wintypes.HANDLE(job),
        JOB_BASIC_ACCOUNTING_INFORMATION,
        ctypes.byref(buffer),
        ctypes.sizeof(buffer),
        None,
    ):
        return None
    return int(ctypes.c_uint32.from_buffer(buffer, ACTIVE_PROCESSES_OFFSET).value)


def break_group(pid: int) -> bool:
    """Ask the child's console group to stop -- the closest thing to SIGTERM.

    The child leads its own group (``CREATE_NEW_PROCESS_GROUP``), so the
    event reaches it and every descendant that stayed in that group. One that
    created a group of its own does not get it -- what holds *that* one is the
    job, not this. Fails where there is no console at all, which is exactly
    where there was nothing graceful to attempt.
    """
    return bool(
        kernel32().GenerateConsoleCtrlEvent(
            signal.CTRL_BREAK_EVENT, wintypes.DWORD(pid)
        )
    )


def attach_job(proc: subprocess.Popen[bytes]) -> None:
    """Hold the child in a job, best effort -- `taskkill` remains the fallback.

    Deliberately *not* part of spawning. Raising from there put an already
    running child into the branch that means "the command never started": the
    handle was never bound, so nothing terminated it and it went on writing
    into a log whose digest was about to be sealed.

    Descendants the child creates before this point are outside the job. A
    plain `Popen` cannot start suspended -- CPython closes the thread handle
    immediately -- so the window is not closable from here; it is documented
    in the status model instead of pretended away.
    """
    with contextlib.suppress(OSError):
        job = create_job()
        if job is None:
            return
        if assign_job(job, proc.pid):
            setattr(proc, JOB_ATTR, job)
        else:
            close(job)


def _tree_alive(proc: subprocess.Popen[bytes], job: int | None) -> bool:
    """Is anything left of the run? The job knows; the direct child does not.

    Watching the child repeats the mistake the POSIX path had to unlearn: a
    wrapper like ``uv`` dies on the first ask within milliseconds while the
    tool it launched is still writing its report, so the wait would end at
    once and the job close would take that tool mid-write -- a zero-length
    grace, and a digest that differs from the POSIX one for the same timeout.
    """
    if job is not None:
        active = job_active(job)
        if active is not None:
            return active > 0
    return pid_alive(proc.pid)


def terminate_tree(
    proc: subprocess.Popen[bytes], grace: float, poll_seconds: float
) -> None:
    """Mirror the POSIX contract: ask, wait, then take the whole tree.

    ``CTRL_BREAK`` stands in for ``SIGTERM`` and the job for the process
    group. The ask only reaches a tool that installs a handler for it -- the
    default one exits the process at once -- so this buys a shutdown window
    for tools that want one, not for every tool.
    """
    # Detach the record first: a second Ctrl-C between the close and the
    # delete would otherwise close the same handle twice, and in the MCP
    # server's thread pool that slot may already belong to someone else.
    job = getattr(proc, JOB_ATTR, None)
    if job is not None:
        with contextlib.suppress(AttributeError):
            delattr(proc, JOB_ATTR)

    if break_group(proc.pid):
        # Only wait when the ask was delivered. A process with no console --
        # an MCP host, a service -- can never receive it, and waiting anyway
        # would add a dead grace period to every single stop.
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if not _tree_alive(proc, job):
                break
            time.sleep(poll_seconds)

    if job is not None:
        close(job)  # KILL_ON_JOB_CLOSE takes whatever is still in it
        return
    if pid_alive(proc.pid):
        # Without a job there is only this. Skipped when the child is already
        # gone, so the second call from `execute`'s finally costs nothing.
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
