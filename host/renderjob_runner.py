"""Start one render in a process that outlives the request.

The switch behind PR #272's worker. Two ways to start it, one seam:

- hosted: a Container Apps Job execution. The app asks ARM to start the job and
  passes the job id as the container's args. Auth is the app's own managed
  identity, so no second copy of a credential lives in the app.
- anywhere else: a detached local process, `python3 -m host.renderjob <id>`,
  but only when the operator asks for it (FIRESIDE_RENDER_LOCAL=1). This is the
  DIY path: no Azure, a long book survives closing the terminal. Off by default
  because a box that used to render in the request now renders elsewhere, and
  that is an operator's decision, not an upgrade's.

If neither can start, start() returns False and the caller keeps its in-process
thread. A box without the infrastructure still renders, it just is not durable;
that is a worse property, not a broken one.

Why an env var and not discovery: the app should not have to guess the resource
id of the job it belongs to. FIRESIDE_RENDER_JOB is that job's full ARM id:
  /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.App/jobs/<name>
"""

import json
import os
import subprocess
import sys
import urllib.request
import urllib.parse

ARM = "https://management.azure.com"
IDENTITY_API = "2019-08-01"

# Children we started and have not reaped. Holding the handle also keeps the
# worker's pid from being collected while it still runs; a long-lived host must
# not leave zombies behind it either, so one daemon thread reaps them.
_children = []
_reaper = None


def _reap():
    global _reaper
    import threading
    if _reaper is not None:
        return
    def loop():
        while True:
            for p in list(_children):
                if p.poll() is not None:
                    _children.remove(p)
            threading.Event().wait(5)
    _reaper = threading.Thread(target=loop, name="fireside-renderjob-reaper",
                               daemon=True)
    _reaper.start()


def _say(msg):
    """Say it on stdout, like the startup line. The console log is the receipt a
    person can actually read; a log level nobody configured prints nothing."""
    print("renderjob: " + msg, flush=True)


def _identity_token(resource=ARM + "/"):
    """A token for ARM from the app's managed identity, or None if this box has
    no identity (every self-host box). Container Apps puts these in the env.

    A user-assigned identity must be named in the request: the endpoint answers
    400 for a bare resource when the identity is not system-assigned (found
    11-12, first live render). A system-assigned box has one identity and needs
    no client_id, so naming one is optional and harmless there.
    """
    endpoint = (os.environ.get("IDENTITY_ENDPOINT") or "").strip()
    header = (os.environ.get("IDENTITY_HEADER") or "").strip()
    if not endpoint or not header:
        return None
    client_id = (os.environ.get("FIRESIDE_MSI_CLIENT_ID")
                 or os.environ.get("AZURE_CLIENT_ID") or "").strip()
    url = (endpoint + "?resource=" + urllib.parse.quote(resource)
           + "&api-version=" + IDENTITY_API)
    if client_id:
        url += "&client_id=" + urllib.parse.quote(client_id)
    req = urllib.request.Request(url, headers={"X-IDENTITY-HEADER": header})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return (json.load(r) or {}).get("access_token")
    except Exception as e:
        _say("identity token failed: %s" % e)
        return None


def start_hosted(job_id, resource_id, token=None):
    """Start one execution of the Container Apps Job `resource_id`.

    No container override: overriding anything at start requires the whole
    container spec, image included, which would put the image name in the app's
    config. The job template already runs the drainer, and the row it needs is
    the one this request just queued. Returns the execution name or None.
    """
    token = token or _identity_token()
    if not token:
        return None
    body = b"{}"
    url = (ARM + resource_id + "/start?api-version=2024-03-01")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Authorization": "Bearer " + token,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r) or {}
        name = d.get("name") or ""
        _say("started ACA execution %s for job %s" % (name or "(no name)", job_id))
        return name or "started"
    except Exception as e:
        detail = ""
        if hasattr(e, "read"):
            try:
                detail = e.read().decode()[:400]
            except Exception:
                pass
        _say("ACA start failed for job %s: %s %s" % (job_id, e, detail))
        return None


def start_local(job_id, log_dir=None):
    """Run the worker detached, the way a DIY user would from a cron entry.

    stdout and stderr go to a file so the run leaves a record (the worker prints
    the row's final status). Returns the pid, or None if it could not spawn.
    """
    out = None
    log_dir = log_dir or os.environ.get("FIRESIDE_DATA") or "/tmp"
    try:
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, "renderjob-%s.log" % job_id)
        out = open(path, "ab", buffering=0)
        p = subprocess.Popen([sys.executable, "-m", "host.renderjob", job_id],
                             stdout=out, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, close_fds=True,
                             start_new_session=True)
        _children.append(p)
        _reap()
        _say("started local worker pid %s for job %s (log %s)"
             % (p.pid, job_id, path))
        return p.pid
    except Exception as e:
        _say("local worker failed for job %s: %s" % (job_id, e))
        return None
    finally:
        if out is not None:
            out.close()


def start(job_id, resource_id=None, log_dir=None):
    """Start the render for `job_id` out of process.

    Returns "job" (a Container Apps execution), "local" (a detached process), or
    None (nothing took it). The caller falls back to its in-process thread only
    on None, and marks the row's owner from what this returns, so a later boot
    cannot mistake a live worker for a dead request thread.
    """
    rid = resource_id or (os.environ.get("FIRESIDE_RENDER_JOB") or "").strip()
    if rid:
        res = start_hosted(job_id, rid)
        if res:
            return "job"
        # A configured hosted job that will not start is a real failure, but the
        # reader is waiting: fall through to a local process rather than leave
        # the book queued.
        _say("hosted start failed for job %s; falling through to the local "
             "path if it is enabled" % job_id)
    if (os.environ.get("FIRESIDE_RENDER_LOCAL") or "").strip() in ("1", "true", "yes"):
        return "local" if start_local(job_id, log_dir=log_dir) else None
    return None


__all__ = ["start", "start_hosted", "start_local", "_identity_token"]