"""Hold this replica awake while a render still has work to do.

A conversion is a thread in this process, not a row a separate worker picks up.
The hosted deployment runs with minReplicas=0, so the platform shuts the only
replica down a few minutes after the last HTTP request. Shutting it down kills
every queued and running thread with it, and the next boot marks them "stalled":
the reader queued four books, one finished, and three said "stopped" with no
explanation (Cat, 11-11).

The fix cannot be "make the render survive a shutdown" alone, because a render
that keeps getting shut down never finishes. So while a job is queued or
running this process asks its own public address for /health about once a
minute. That is a real request through the ingress, which is exactly what the
scale rule watches. When nothing is left to render it stops asking and the
replica is free to sleep again.

Why its own address and not localhost: a request to the local port never leaves
the container, so the platform's scaler never sees it.
"""

import json
import os
import threading
import time
import urllib.request

from host import db as _db
from host import jobs as _jobs

INTERVAL_S = 45
# Six hours of continuous pings with no job ever finishing means something is
# wedged (a thread died without a restart, a job row that will never move).
# Stop asking then, so a stuck row cannot hold the replica up forever.
MAX_PINGS = int(6 * 3600 / INTERVAL_S)


def _base_url():
    return (os.environ.get("FIRESIDE_BASE_URL") or "").strip().rstrip("/")


def _any_active(conn):
    try:
        return _jobs.any_active(conn)
    except Exception:
        return False


def start(db_path=None, base_url=None, interval=INTERVAL_S):
    """Start the keeper thread. Returns the thread, or None when there is no
    public address to ask (a local run has nothing to keep awake)."""
    base = (base_url or _base_url())
    if not base.startswith("http"):
        return None

    def _run():
        pings = 0
        while pings < MAX_PINGS:
            conn = None
            try:
                conn = _db.connect(db_path)
                active = _any_active(conn)
            except Exception:
                active = False
            finally:
                try:
                    if conn is not None:
                        conn.close()
                except Exception:
                    pass
            if active:
                pings += 1
                try:
                    req = urllib.request.Request(base + "/health",
                                                 headers={"User-Agent": "fireside-keepwarm"})
                    with urllib.request.urlopen(req, timeout=10) as r:
                        r.read(256)
                except Exception:
                    pass                    # a failed ping must never stop a render
            time.sleep(interval)

    t = threading.Thread(target=_run, name="fireside-keepwarm", daemon=True)
    t.start()
    return t