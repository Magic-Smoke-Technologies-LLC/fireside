"""A stopped conversion has to say what stopped it, and offer the one action.

Cat queued four books for voicing. One finished and three said "stopped", with
no reason, no button, and no way to tell whether they were still waiting their
turn. They were not: the hosted replica went to sleep and the boot after it
swept every queued run to "stalled". The panel printed a state with no cause and
no next step.

The test seeds a row exactly as that boot leaves it, then reads what she sees:
the reason in words, and a Resume that starts a new run for the same book and
voice through the same door the convert dialog uses.
"""

import os
import sqlite3
import time
import unittest

from tests_ui.support import (BrowserTest, cookie_jar, wait_until,
                              wait_visible)


class TestConvPanel(BrowserTest):

    def db(self):
        c = sqlite3.connect(os.path.join(self.host.data, "fireside.db"))
        c.row_factory = sqlite3.Row
        return c

    def seed_stalled(self, book_id, voice=""):
        """The row a restart leaves behind: queued, then swept to stalled."""
        c = self.db()
        try:
            uid = c.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            c.execute(
                "INSERT INTO jobs(id,user_id,book_id,kind,provider,voice,model,"
                "status,total,current,error,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("seedstalled0000000000000000000001", uid, book_id, "render",
                 "local", voice, "", "stalled", 3, 1,
                 "interrupted; converted chapters are kept, run the convert "
                 "again to resume", now, now))
            c.commit()
        finally:
            c.close()

    def jobs_for(self, book_id):
        status, body = self.host.request("/jobs?user=",
                                         cookies=cookie_jar(self.context))
        return [j for j in (body or {}).get("jobs", []) if j.get("book_id") == book_id]

    def voice_id(self):
        status, body = self.host.request(
            "/connections", cookies=cookie_jar(self.context), method="POST",
            body={"provider": "local", "label": "Offline tone"})
        self.assertEqual(status, 200, "no local voice: %r" % (body,))
        cid = body["id"]
        status, voices = self.host.request(
            "/voices", cookies=cookie_jar(self.context), method="POST",
            body={"connection_id": cid})
        self.assertEqual(status, 200, "no voice list: %r" % (voices,))
        return cid, (voices.get("voices") or [{}])[0].get("id", "")

    def test_a_stopped_run_says_why_and_offers_resume(self):
        self.register("ui_convpanel")
        status, add = self.host.request(
            "/book/example", cookies=cookie_jar(self.context), method="POST",
            body={"user": "ui_convpanel", "render": False})
        self.assertEqual(status, 200, "the example book did not import: %r" % (add,))
        cid, voice = self.voice_id()
        self.assertTrue(voice, "the local voice has no id")
        self.seed_stalled(add["book_id"], voice)

        self.page.reload()
        self.wait_library(1)
        wait_visible(self.page, "#convjobs")

        box = self.page.text_content("#convjobs")
        self.assertIn("stopped", box, "the panel does not say the run is stopped")
        self.assertIn("restarted", box,
                      "the panel gives no reason the reader can act on")
        # The reason must not be the server's own log sentence.
        self.assertNotIn("run the convert again", box)

        clicked = self.page.evaluate(
            "() => { const b = [...document.querySelectorAll('#convjobs button')]"
            ".find(x => x.textContent.trim() === 'Resume');"
            " if (!b) return false; b.click(); return true; }")
        self.assertTrue(clicked, "a stopped run has no Resume button")

        def resumed():
            rows = self.jobs_for(add["book_id"])
            return any(j["id"] != "seedstalled0000000000000000000001"
                       and j["status"] in ("queued", "running", "done")
                       for j in rows)
        wait_until(self.page, "() => true")   # let the POST reach the host
        deadline = time.time() + 20
        while time.time() < deadline and not resumed():
            time.sleep(0.3)
        self.assertTrue(resumed(),
                        "Resume did not start a new run for the same book: %r"
                        % (self.jobs_for(add["book_id"]),))


if __name__ == "__main__":
    unittest.main()