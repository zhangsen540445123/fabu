"""Single process ownership cannot expire during sleep or a slow encoder."""

import hashlib
from collections.abc import Callable
from uuid import uuid4

import win32api
import win32con
import win32event
import win32security
import winerror

from fabu.domain import JobSnapshot
from fabu.store import JobStore


class AlreadyRunning(RuntimeError):
    pass


class SingleInstance:
    def __init__(self, key: str):
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            user = win32security.ConvertSidToStringSid(sid)
        finally:
            token.Close()
        identity = user + "\0" + key.casefold()
        self.name = "Global\\FabuWorker-" + hashlib.sha256(identity.encode()).hexdigest()
        self.handle = None

    def __enter__(self):
        handle = win32event.CreateMutex(None, True, self.name)
        if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
            handle.Close()
            raise AlreadyRunning("Another Fabu worker already owns this data directory")
        self.handle = handle
        return self

    def __exit__(self, *_):
        if self.handle is not None:
            win32event.ReleaseMutex(self.handle)
            self.handle.Close()
            self.handle = None


class Worker:
    def __init__(self, store: JobStore):
        self.store = store
        self.id = str(uuid4())
        self.lock = SingleInstance(str(store.path))
        self.active = False

    def __enter__(self):
        self.lock.__enter__()
        try:
            self.store.recover_interrupted()
            self.active = True
            return self
        except BaseException:
            self.lock.__exit__()
            raise

    def __exit__(self, *_):
        self.active = False
        self.lock.__exit__()

    def run_once(self, handler: Callable[[JobSnapshot], None]) -> bool:
        if not self.active:
            raise RuntimeError("Worker must hold its single-instance context")
        job = self.store.claim_next(self.id)
        if job is None:
            return False
        try:
            handler(job)
        except Exception:
            current = self.store.get(job.id)
            if current.state not in {"PAUSED", "FAILED", "CANCELED"}:
                self.store.transition(job.id, current.revision, "PAUSED", "stage_failed")
        return True
