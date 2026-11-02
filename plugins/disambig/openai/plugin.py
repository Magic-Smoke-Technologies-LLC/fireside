"""v2 disambig plugin: any OpenAI-compatible chat endpoint.

The host hands over the sentences that are about to be voiced; this plugin
returns only the spots it is not sure about, as questions the host can render.
Nothing here knows what a chapter is, and the host never learns what an
OpenAI-compatible API looks like: it declares `base_url` and `key`, the user
points it at their own box (a llama.cpp or Ollama server on the LAN, or a
hosted one), and the models come from that server's own list.

Why the sentence filter exists: a whole 1.58M-char book is ~395k tokens, and
most of it cannot be mispronounced. Only sentences holding a known
two-reading word are worth paying to read.
"""
import json
import re
import urllib.error
import urllib.request

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from host.contracts import Disambig as Base, Field, Quote

DEFAULT_BASE = "https://api.openai.com/v1"
_TIMEOUT = 60
MAX_FIXES = 200                # one book, not a dictionary

# Words with two readings that change the sound of the sentence around them.
# Not a dictionary: a hit means "worth asking", not "ask".
CANDIDATES = (
    "live", "read", "lead", "wind", "wound", "tear", "row", "bow", "close",
    "does", "use", "used", "house", "minute", "present", "object", "record",
    "desert", "content", "invalid", "polish", "produce", "refuse", "resume",
    "sow", "bass", "dove", "number", "learned", "blessed", "aged", "bowed",
    "entrance", "excuse", "abuse", "graduate", "separate", "moderate",
)

# What the well-known models cost, USD per 1M tokens (in, out). A model not in
# here is quoted as unknown, never as free.
PRICES = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-5-mini": (0.25, 2.00),
}

_ASK = (
    "You find words whose pronunciation a text-to-speech engine could get wrong "
    "because they have more than one reading. For each one, ask a question whose "
    "answers are the two readings, using a few words of the sentence as the hint. "
    "Only report a word when the sentence decides it but the bare word does not; "
    "skip anything you would pronounce the obvious way. Reply with JSON only: "
    '{"questions":[{"word":"live","question":"...","options":["...","..."]}]}. '
    "An empty list is a good answer."
)


def _sentences(text):
    """Split on sentence ends, keeping the text intact for the caller."""
    return [s for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]


def _candidates(text):
    """Sentences that hold at least one two-reading word, longest first."""
    out = []
    for s in _sentences(text):
        low = " " + re.sub(r"[^a-z]+", " ", s.lower()) + " "
        if any(" %s " % w in low for w in CANDIDATES):
            out.append(s)
    return out


def _post(url, key, payload):
    body = json.dumps(payload).encode()
    hdr = {"Content-Type": "application/json"}
    if key:
        hdr["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=body, headers=hdr, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError("the model server refused the request (%s)" % e.code)
    except urllib.error.URLError:
        raise RuntimeError("the model server did not answer")
    except (TimeoutError, OSError):
        raise RuntimeError("the model server did not answer")



_DECIDE = (
    "You are preparing text for a narrator. Some words have more than one "
    "reading, and the sentence decides which one it is. For each one, write the "
    "word the way it SOUNDS so a text-to-speech engine says it right, using "
    "ordinary spelling and no phonetic symbols: \"live\" in \"we live here\" is "
    "\"liv\", and \"live\" in \"a live broadcast\" is \"lyve\". Only report a "
    "word when the sentence settles it and the plain spelling could be read the "
    "other way. Reply with JSON only: "
    '{"fixes":[{"word":"live","say":"liv","why":"we live here"}]}. '
    "Fewer is better. An empty list is a good answer."
)


def _clean_fix(item):
    """One decision, or None. A model that answers in prose gets dropped here."""
    if not isinstance(item, dict):
        return None
    word, say = (item.get("word") or ""), (item.get("say") or "")
    if not isinstance(word, str) or not isinstance(say, str):
        return None
    word, say = word.strip(), " ".join(say.split())
    if not word or not say or len(word) > 40 or len(say) > 60:
        return None
    if say.lower() == word.lower():
        return None
    return {"word": word, "say": say, "why": (item.get("why") or "").strip()[:160]}


class Disambig(Base):
    id = "openai"
    needs_key = False

    def settings(self):
        return [
            Field("base_url", "Server URL", "text", default=DEFAULT_BASE,
                  help="Any OpenAI-compatible chat server: a llama.cpp or Ollama "
                       "box on your LAN, or a hosted one. Runs on your hardware "
                       "when it is yours."),
            Field("key", "Key", "password",
                  help="Bearer token, if the server wants one. Empty for a local box."),
        ]

    def _base(self, cred):
        return ((cred or {}).get("base_url") or DEFAULT_BASE).rstrip("/")

    # ---- what it can run ---------------------------------------------------

    def list_models(self, cred):
        """Ask the configured server. Prices come from our table because a local
        server costs nothing and a hosted one will not tell us."""
        hdr = {}
        if (cred or {}).get("key"):
            hdr["Authorization"] = "Bearer " + cred["key"]
        req = urllib.request.Request(self._base(cred) + "/models", headers=hdr)
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
                data = json.loads(r.read().decode())
        except Exception:
            return []
        out = []
        for m in data.get("data") or []:
            mid = m.get("id") or ""
            if not mid:
                continue
            pin, pout = PRICES.get(mid, (None, None))
            out.append({"id": mid, "label": mid,
                        "price_per_1m": pin, "price_per_1m_out": pout})
        return out

    def quote(self, ctx):
        """One pass over this chapter, on this model, before it runs."""
        text = (ctx or {}).get("text") or ""
        cand = _candidates(text)
        chars = sum(len(s) for s in cand)
        model = (ctx or {}).get("model") or ""
        pin, pout = PRICES.get(model, (None, None))
        if pin is None:
            # We will not print a number we cannot defend. Unknown is unknown.
            return Quote(chars_est=chars, price=0.0, eligible=True,
                         authoritative=False,
                         refusal_reason="no published price for %s" % (model or "that model"))
        tin = chars / 4.0 / 1e6 * pin
        tout = (chars / 4.0 * 0.25) / 1e6 * pout
        return Quote(chars_est=chars, price=round(tin + tout, 6), eligible=True,
                     authoritative=True)

    # ---- the pass ----------------------------------------------------------

    def scan(self, sentences, model, cred):
        cand = _candidates(sentences if isinstance(sentences, str) else " ".join(sentences or []))
        if not cand:
            return []
        if not model:
            raise RuntimeError("no model chosen")
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": _ASK},
                         {"role": "user", "content": "\n".join(cand)}],
            "response_format": {"type": "json_object"},
        }
        data = _post(self._base(cred) + "/chat/completions", (cred or {}).get("key"), payload)
        try:
            raw = data["choices"][0]["message"]["content"]
            got = json.loads(raw)
        except (KeyError, IndexError, TypeError, ValueError):
            raise RuntimeError("the model answered in a shape we cannot read")
        out = []
        for q in got.get("questions") or []:
            word = (q.get("word") or "").strip()
            opts = [o for o in (q.get("options") or []) if isinstance(o, str)]
            if not word or len(opts) < 2:
                continue
            out.append({"word": word, "question": (q.get("question") or "").strip(),
                        "options": opts[:4]})
        return out

    def decide(self, sentences, model, cred):
        """The same pass, answered instead of asked: [{word, say, why}].

        For a host that runs this on its own box for a reader who should not have
        to answer twenty questions before hearing a chapter. The answers come
        back as pronunciation rules, so the pass runs once per book and the
        engine never hears the wrong reading to begin with.
        """
        cand = _candidates(sentences if isinstance(sentences, str) else " ".join(sentences or []))
        if not cand:
            return []
        if not model:
            raise RuntimeError("no model chosen")
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": _DECIDE},
                         {"role": "user", "content": "\n".join(cand)}],
            "response_format": {"type": "json_object"},
        }
        data = _post(self._base(cred) + "/chat/completions", (cred or {}).get("key"), payload)
        try:
            raw = data["choices"][0]["message"]["content"]
            got = json.loads(raw)
        except (KeyError, IndexError, TypeError, ValueError):
            raise RuntimeError("the model answered in a shape we cannot read")
        out, seen = [], set()
        for item in got.get("fixes") or []:
            fix = _clean_fix(item)
            if fix and fix["word"].lower() not in seen:
                seen.add(fix["word"].lower())
                out.append(fix)
        return out[:MAX_FIXES]
