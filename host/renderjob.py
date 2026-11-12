"""Run one queued render, in its own process, from the job row on disk.

    python3 -m host.renderjob <job id>

Why this exists: on the hosted box a render used to be a thread inside the web
process, and the platform is free to stop that process (KEDAScaleTargetDeactivated
1->0 on 11-11 killed three of Cat's four queued books). A job is durable because
the work is not inside the request process: the row holds everything the worker
needs, and a stopped worker leaves a row that can be run again.

The same command is the DIY user's escape hatch: run a long book under systemd or
a cron entry and close the terminal. Nothing here needs Azure; only the thing
that *starts* the process does (see host/renderjob_runner.py).

Everything the request already decided is read from the row: the connection, the
voice, the model, the speed, the characters it quoted and the reservation it
made. What the worker must not do is invent any of it.
"""

import argparse
import json
import os
import sys

from host import accounting, connections, credentials, db, jobs, notices, pron
from host.host import Host
from host.db import now


def _rules(conn, user, book_id):
    return pron.for_book(pron.load(conn, user), book_id,
                         pron.book_author(conn, book_id),
                         pron.book_series(conn, book_id))


def _cred(conn, user, provider, cid):
    """The speech credential for this run: its own connection when the row names
    one, else whatever the account stored for the provider."""
    if cid:
        cred = connections.resolve(conn, user, cid)
        if cred is None:
            raise RuntimeError("connection %s is switched off or gone" % cid)
        return cred
    return credentials.get_credential(conn, user, provider) or {}


def _finish_notice(conn, user, book_id, job_id):
    """Tell the bell how the run ended, reading the row back rather than
    guessing: a render reports failure by writing a status, not by raising."""
    j = jobs.job(conn, job_id) or {}
    st = j.get("status")
    if st not in ("done", "error"):
        return
    r = conn.execute("SELECT title FROM books WHERE id=?", (book_id,)).fetchone()
    title = ((r["title"] if r else "") or "").strip() or "your book"
    try:
        if st == "done":
            notices.notify(conn, user, "job", "%s is ready to play." % title,
                           route="#/play/" + str(book_id), key="run:" + job_id)
        else:
            notices.notify(conn, user, "job", "%s stopped before it finished." % title,
                           route="#/play/" + str(book_id), key="run:" + job_id)
    except Exception:
        pass                      # a notice must never fail a finished run


def drain(plugins_root=None, echo=print, limit=20):
    """Run every queued row, one at a time, then stop.

    This is what the hosted job's container runs: no argument names the row,
    because the queue row is the work order and a per-execution argument would
    make the *starter* carry details of the image. One host, many rows.
    """
    conn = db.connect(None)
    done = 0
    try:
        host = Host(plugins_root=plugins_root)
        while done < limit:
            jid = jobs.claim_queued(conn)
            if not jid:
                break
            echo("claimed %s" % jid)
            _run_with(conn, host, jid, echo)
            done += 1
    finally:
        try:
            conn.close()
        except Exception:
            pass
    echo("drained %d job(s)" % done)
    return 0


def run(job_id, plugins_root=None, echo=print):
    conn = db.connect(None)
    try:
        return _run_with(conn, Host(plugins_root=plugins_root), job_id, echo)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _only(row):
    """The chapters this row was asked to make, or None for the whole book."""
    raw = (row.get("only_idx") if isinstance(row, dict) else row["only_idx"]) or ""
    if not raw:
        return None
    try:
        v = json.loads(raw)
    except ValueError:
        return None
    return [int(i) for i in v] if isinstance(v, list) else None


def _run_with(conn, host, job_id, echo=print):
    """Run the job row `job_id` to completion. Returns a POSIX exit code."""
    try:
        row = jobs.job(conn, job_id)
        if not row:
            echo("no job %s" % job_id)
            return 3
        if row.get("status") not in ("queued", "running"):
            # Someone cancelled it, or it already finished. Running it now would
            # either ignore the reader or charge twice; settle() would refuse the
            # second charge, but the audio would still be made. Stop.
            echo("job %s is %s; nothing to do" % (job_id, row.get("status")))
            return 0

        user = row.get("user_id") or ""
        book_id = row.get("book_id") or ""
        provider = row.get("provider") or ""
        cid = row.get("connection_id") or ""
        voice = row.get("voice") or ""
        model = row.get("model") or ""
        speed = float(row.get("speed") or 1.0)
        res_id = row.get("reservation_id") or ""
        fresh = int(row.get("quoted_chars") or 0)

        if provider not in host.speech:
            echo("speech provider %r is not loaded here" % provider)
            return 4
        speech = host.speech[provider].obj
        storage = host.storage[host.storage_backend()].obj
        cred = _cred(conn, user, provider, cid)

        rows = conn.execute("SELECT idx FROM chapters WHERE book_id=? ORDER BY idx",
                            (book_id,)).fetchall()
        todo = [r["idx"] for r in rows]
        # The row can carry a selection (a repair, a single chapter). This
        # process has no request to ask, so the selection must have been
        # written down when it was queued.
        only = _only(row)
        if only is not None:
            want = set(only)
            todo = [i for i in todo if i in want]
        rules = _rules(conn, user, book_id)

        def cancelled():
            return jobs.status_of(conn, job_id) == "cancelled"

        jobs.render_book(conn, storage, speech, user, book_id, voice,
                         model, speed, cred=cred, opts={"model": model, "speed": speed},
                         only=only, job_id=job_id, plugins_root=host.plugins_root,
                         should_stop=cancelled)

        if res_id:
            # The request reserved; this process settles, because it is the one
            # that knows which chapters actually landed.
            accounting.settle_run(conn, storage, speech, book_id, todo, rules,
                                  voice, model, speed, res_id, fresh, cred, job_id)

        _finish_notice(conn, user, book_id, job_id)
        j = jobs.job(conn, job_id) or {}
        echo("job %s: %s (%s/%s)" % (job_id, j.get("status"), j.get("current"),
                                     j.get("total")))
        return 0 if j.get("status") in ("done", "cancelled") else 1
    except Exception as e:                 # a worker must leave a status, not a trace
        try:
            jobs.mark_error(conn, job_id, "%s: %s" % (type(e).__name__, e))
        except Exception:
            pass
        echo("job %s failed: %s" % (job_id, e))
        return 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m host.renderjob",
                                 description="Run one queued render from its job row.")
    ap.add_argument("job", nargs="?", help="job id (the row is the work order)")
    ap.add_argument("--drain", action="store_true",
                    help="run every queued row and stop; this is what the hosted "
                         "job container runs, so nothing outside has to name a row")
    ap.add_argument("--plugins", default=os.environ.get("FIRESIDE_PLUGINS_ROOT"),
                    help="plugins root (default: the checkout's ./plugins)")
    a = ap.parse_args(argv)
    if a.drain:
        return drain(plugins_root=a.plugins)
    if not a.job:
        ap.error("either a job id or --drain")
    return run(a.job, plugins_root=a.plugins)


if __name__ == "__main__":
    sys.exit(main())