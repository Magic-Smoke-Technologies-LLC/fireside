"""Hold this replica awake while a render has work to do.

A conversion is a thread in this process, not a row a separate worker picks up.
The hosted deployment runs with minReplicas=0 and the platform does scale it to
zero: the system log for 11-11 shows `KEDAScaleTargetDeactivated ... from 1 to 0`
at 21:26:15, then an activation at 21:27:56 on an inbound request. Deactivating
the only replica killed Cat's three queued books, and the boot after it swept
them to stalled ("stopped", with nothing on screen to explain it).

While a render is live this process asks its own public address for /health
about once a minute. That is a real request through the ingress, which is what
the scaler counts (`s0-upstream_rq_total` is the trigger it named when it woke
up). When nothing is left to render it stops asking and the replica is free to
sleep again.

Why its own public address and not localhost: a request to the local port never
leaves the container, so the platform's scaler never sees it.

Why an in-process counter and not a query: the first version asked the database
whether a job was queued, and it never pinged on the live box (the request
metric stayed at the two health probes a minute). A keeper that can fail
silently is not a keeper. The count lives in host/jobs.py, next to the renders
it counts.
"""

import os
import threading
import time
import urllib.request

from host import jobs as _jobs

INTERVAL_S = 45
# Six hours of continuous pings with no render ever finishing means something
# is wedged. Stop asking then, so a leak cannot hold the replica up forever.
MAX_PINGS = int(6 * 3600 / INTERVAL_S)

_started = None


def _say(msg):
    """Say it on stdout, like the startup line.

    The first version logged at INFO and nothing was configured to print INFO,
    so the keeper's own evidence never reached the console log: installed,
    silent, and impossible to check. A print always lands there.
    """
    print("keepwarm: " + msg, flush=True)


def _base_url():
    return (os.environ.get("FIRESIDE_BASE_URL") or "").strip().rstrip("/")


def _ping(base):
    req = urllib.request.Request(base + "/health",
                                 headers={"User-Agent": "fireside-keepwarm"})
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read(256)


def tick(base):
    """One pass of the keeper: one request if a render is live, nothing if not.

    Separate from the thread so the decision can be tested without waiting on
    a clock: `render live -> ping`, `nothing live -> silent`.
    """
    if _jobs.active_renders() <= 0:
        return False
    _ping(base)
    return True


def start(base_url=None, interval=INTERVAL_S):
    """Start the keeper thread once per process. Returns the thread, or None
    when there is no public address to ask (a local run has nothing to keep
    awake)."""
    global _started
    base = (base_url or _base_url())
    if not base.startswith("http"):
        return None
    if _started is not None and _started.is_alive():
        return _started

    def _run():
        pings = 0
        holding = False
        failed_once = False
        while pings < MAX_PINGS:
            try:
                fired = tick(base)
            except Exception as e:      # a failed ping must never stop a render
                fired = True            # the render is live; keep asking
                if not failed_once:
                    failed_once = True
                    _say("keep-alive request failed: %s" % e)
            if fired:
                pings += 1
                if not holding:
                    holding = True
                    _say("holding the replica awake while a render runs")
            elif holding:
                holding = False
                _say("nothing left to render, letting the replica sleep")
            time.sleep(interval)

    t = threading.Thread(target=_run, name="fireside-keepwarm", daemon=True)
    t.start()
    _started = t
    return t
