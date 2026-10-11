"""Per-key locks that serialise work on one interview / answer within this server process.

They avoid duplicate LLM calls when the same request is repeated or raced. Correctness does not depend
on them: the unique indexes in the database are the real guard against duplicate rows.
"""

import threading
from contextlib import contextmanager

_registry_lock = threading.Lock()
_locks: dict = {}


@contextmanager
def keyed_lock(namespace: str, key):
    with _registry_lock:
        lock = _locks.setdefault((namespace, key), threading.Lock())
    with lock:
        yield
