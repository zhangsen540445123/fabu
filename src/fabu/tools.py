"""Bounded, literal command execution. Windows children are owned by a Job Object."""

import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import psutil
import win32api
import win32con
import win32job
import win32process
from pydantic import BaseModel, ConfigDict, Field, field_validator


class CommandSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    executable: Path
    args: list[str]
    cwd: Path
    env: dict[str, str]
    timeout_s: float = Field(gt=0, le=86400)
    max_output_bytes: int = Field(default=4_194_304, ge=128, le=67_108_864)

    @field_validator("executable")
    @classmethod
    def validate_executable(cls, path: Path) -> Path:
        if not path.is_absolute() or not path.is_file() or path.suffix.lower() != ".exe":
            raise ValueError(
                "Tool must be an existing absolute .exe executable, never a shell file"
            )
        return path

    @field_validator("cwd")
    @classmethod
    def validate_cwd(cls, path: Path) -> Path:
        if not path.is_absolute() or not path.is_dir():
            raise ValueError("Working directory must be an existing absolute directory")
        return path

    @field_validator("args")
    @classmethod
    def no_nul(cls, args: list[str]) -> list[str]:
        if any("\0" in arg for arg in args):
            raise ValueError("Arguments cannot contain NUL")
        return args


class CommandResult(BaseModel):
    exit_code: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    canceled: bool = False
    output_limit_exceeded: bool = False


def _start_owned(spec: CommandSpec, stdout, stderr):
    job = win32job.CreateJobObject(None, "")
    limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    limits["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
    proc = None
    try:
        # Suspend before assignment: child code cannot spawn an unowned descendant in the gap.
        proc = subprocess.Popen(
            [str(spec.executable), *spec.args],
            cwd=spec.cwd,
            env=spec.env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW | win32con.CREATE_SUSPENDED,
        )
        handle = win32api.OpenProcess(win32con.PROCESS_ALL_ACCESS, False, proc.pid)
        try:
            win32job.AssignProcessToJobObject(job, handle)
            threads = psutil.Process(proc.pid).threads()
            if len(threads) != 1:
                raise RuntimeError("Unexpected initial process thread count")
            thread = win32api.OpenThread(win32con.THREAD_SUSPEND_RESUME, False, threads[0].id)
            try:
                win32process.ResumeThread(thread)
            finally:
                thread.Close()
        finally:
            handle.Close()
        return proc, job
    except BaseException:
        if proc is not None:
            proc.kill()
            proc.wait()
        job.Close()
        raise


def run_command(spec: CommandSpec, cancel: threading.Event) -> CommandResult:
    if cancel.is_set():
        return CommandResult(exit_code=-1, stdout=b"", stderr=b"", canceled=True)
    started = time.monotonic()
    timed_out = canceled = exceeded = False
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        proc, job = _start_owned(spec, stdout, stderr)
        try:
            while proc.poll() is None:
                canceled = cancel.is_set()
                timed_out = time.monotonic() - started >= spec.timeout_s
                exceeded = (
                    os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size
                    > spec.max_output_bytes
                )
                if canceled or timed_out or exceeded:
                    win32job.TerminateJobObject(job, 1)
                    break
                time.sleep(0.02)
            proc.wait(timeout=5)
        finally:
            # Also kills descendants left behind by an exited parent.
            job.Close()
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        total = os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size
        exceeded = exceeded or total > spec.max_output_bytes
        stdout.seek(0)
        stderr.seek(0)
        out = stdout.read(spec.max_output_bytes)
        err = stderr.read(spec.max_output_bytes - len(out))
        code = proc.returncode
        if (timed_out or canceled or exceeded) and code == 0:
            code = -1
        return CommandResult(
            exit_code=code,
            stdout=out,
            stderr=err,
            timed_out=timed_out,
            canceled=canceled,
            output_limit_exceeded=exceeded,
        )
