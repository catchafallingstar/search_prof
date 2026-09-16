"""Emit each JSON record and its newline in one synchronized write."""
import json
import sys
import multiprocessing

_lock = multiprocessing.RLock()


def write_event(payload):
    line = json.dumps(payload, ensure_ascii=True, default=str) + '\n'
    with _lock:
        sys.stdout.write(line)
        sys.stdout.flush()
