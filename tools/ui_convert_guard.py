#!/usr/bin/env python3
"""Check the convert dialog against the second before its voice list arrives.

The bug this exists for: the dialog opens with one blank option ("loading…"),
the voice list lands a moment later, and a convert clicked in between asked the
server for voice:"". Every provider has its own idea of a default, so the book
came out in a voice nobody chose, and the wrong-voice chapter looked like a bug
in the book.

The page is served straight out of ui/ and the host's answers are stubbed here,
so this checks the real DOM and the real script, not a copy of the logic. The
stub also stands in for the host's /i18n.js injection (T is the identity), which
is why a missing T here is not a bug in the page.

Runs headless chromium through playwright:

    python3 tools/ui_convert_guard.py

Exit 0 means: for the whole loading window the convert buttons are disabled, a
click does nothing, the dialog stays open, and no render is posted without a
voice.
"""
import http.server
import json
import os
import socketserver
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, "ui")
PORT = 8794
VOICE_DELAY = 1.2          # long enough to click in the middle of it
STUB = {
    "/2fa": {},
    "/auth/policy": {"providers": [], "has_accounts": True, "registration_open": True},
    "/me": {"authenticated": True, "user": "u1"},
    "/last": {}, "/queue": [], "/prefs": {}, "/library": [],
    "/plugins": {"readers": [], "storage": [], "disambig": [],
                 "speech": [{"id": "fake", "label": "Fake", "needs_key": False, "settings": []}]},
    "/connections": [{"id": "c1", "provider": "fake", "label": "Fake"}],
    "/estimate": {"synth_seconds": 10, "audio_bytes": 1000, "audio_seconds": 10},
}
RENDERS = []


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=UI, **k)

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass                    # the page moved on; not this test's business

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in STUB:
            return self._json(STUB[path])
        try:
            return super().do_GET()
        except (BrokenPipeError, FileNotFoundError):
            return          # a page that moved on, or an icon this dir has not got

    def do_POST(self):
        path = self.path.split("?")[0]
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8", "replace")
        if path == "/voices":
            time.sleep(VOICE_DELAY)
            return self._json({"voices": [{"id": "v1", "label": "Voice One"},
                                          {"id": "v2", "label": "Voice Two"}]})
        if path == "/render":
            RENDERS.append(body)
            return self._json({"job_id": "j1"})
        return self._json(STUB.get(path, {}))


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


READ_DIALOG = """() => {
  const b = s => document.querySelector(s);
  return {all: b('#cvall').disabled, sel: b('#cvsel').disabled,
          voice: b('#cvvoice').value, opts: b('#cvvoice').options.length,
          info: b('#cvvoiceinfo').textContent,
          open: !b('#convertdlg').hidden};
}"""


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed; nothing checked")
        return 2
    httpd = Server(("127.0.0.1", PORT), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    bad = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page()
            page.goto("http://127.0.0.1:%d/index.html" % PORT,
                      wait_until="domcontentloaded")
            page.wait_for_timeout(400)
            page.evaluate("""() => {
              if (!window.T) window.T = function (s) { return s; };
              USER = 'u1';
              CONNS = [{id: 'c1', provider: 'fake', label: 'Fake'}];
              PREFS = {default_connection_id: 'c1', default_voice: '', reading: null};
              book = {id: 'b1', title: 'B', chapters: [
                {idx: 0, chars: 100, title: 'One'}, {idx: 1, chars: 100, title: 'Two'}]};
              SELECT = new Set();
              openConvert();
            }""")
            page.wait_for_timeout(60)           # inside the window, list not back
            during = page.evaluate(READ_DIALOG)
            page.evaluate("() => document.querySelector('#cvall').click()")
            page.wait_for_timeout(120)
            clicked = page.evaluate(READ_DIALOG)
            idle = len(RENDERS)          # nothing may have been posted yet
            page.wait_for_timeout(int(VOICE_DELAY * 1000) + 400)
            later = page.evaluate(READ_DIALOG)
            page.evaluate("() => document.querySelector('#cvall').click()")
            page.wait_for_timeout(250)
            after = page.evaluate(READ_DIALOG)
            browser.close()
    finally:
        httpd.shutdown()

    if during["opts"] != 1 or during["voice"]:
        bad.append("the voice list was already back, so the window was not tested")
    if not (during["all"] and during["sel"]):
        bad.append("a convert button was live before a voice existed")
    if not clicked["open"]:
        bad.append("clicking convert during load closed the dialog")
    if idle:
        bad.append("%d render(s) posted while the list was still loading: %s"
                   % (idle, RENDERS[0]))
    if later["all"] or later["sel"] or not later["voice"]:
        bad.append("the buttons did not come alive once voices arrived: %s" % later)
    if after["open"]:
        bad.append("the dialog stayed open after a real convert click")
    if len(RENDERS) != 1:
        bad.append("expected exactly one render from the good click, got %d" % len(RENDERS))
    elif not json.loads(RENDERS[0]).get("voice"):
        bad.append("the render that did go out carried no voice: %s" % RENDERS[0])
    print(json.dumps({"during": during, "clicked": clicked, "later": later,
                      "after": after, "renders": RENDERS}, indent=1))
    if bad:
        for b in bad:
            print("FAIL: " + b)
        return 1
    print("ok: no convert is possible until a voice is chosen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
