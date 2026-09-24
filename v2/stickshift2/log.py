"""Append-only JSON-lines log at ~/.stickshift/v2.log, created 0600 (chat prompts are sensitive)."""
import json
import os
import threading
import time


class Log:
    def __init__(self, path=None):
        self.path = path or os.path.expanduser("~/.stickshift/v2.log")
        self._lock = threading.Lock()

    def write(self, event, **fields):
        rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event}
        rec.update(fields)
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with self._lock:
            d = os.path.dirname(self.path)
            os.makedirs(d, mode=0o700, exist_ok=True)
            if d == os.path.expanduser("~/.stickshift"):
                os.chmod(d, 0o700)
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
