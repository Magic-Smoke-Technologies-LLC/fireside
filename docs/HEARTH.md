# The door's fire

A short loop behind the welcome pane on the first screen. Decoration, with a
budget: it must not cost a phone a megabyte of data to look at a sign-in form.

## Where it lives

| Path | What it is |
| --- | --- |
| `assets/hearth.mp4` | the loop. Absent is a normal state. |
| `assets/hearth-poster.jpg` | one still, the first frame. Optional but it is what paints before the video can. |

Both are optional. When `hearth.mp4` is missing, `GET /hearth.mp4` answers 404,
the page is rendered with `data-has=""` on the hearth element, and the door
keeps its own glow. Nothing in the page asks for the file, so a stock checkout
does not put a 404 in a stranger's console.

## Swapping in a licensed cut

1. Drop the encoded file in at `assets/hearth.mp4` and a still at
   `assets/hearth-poster.jpg`. Nothing else changes: the render notices the
   files at request time, not at build time.
2. Rebuild and deploy. Both files are inside the image, so a deploy is how a
   new cut reaches the site.

The encode the page is built for: 10 to 12 seconds, 720p, under 1.5 MB, no
audio track. A seamless cut matters more than resolution, because the clip
loops and a visible seam at every loop is the thing people notice instead of
the fire.

CHRIS: the licence is the open question. Vecteezy's free tier allows
commercial use with visible attribution to the creator; Pro removes that. The
page has no credit line anywhere, so a free-tier clip needs one, on the door or
in the footer, before it ships.

## What the page refuses

The hearth is mounted by `wireHearth()` in `ui/index.html`, and it declines in
every case where the fire is a cost rather than a welcome:

- `prefers-reduced-motion: reduce`: the element is removed, not just paused.
- `navigator.connection.saveData` or an `effectiveType` of `2g`/`slow-2g`: same.
- under 880px: `display: none`, so a phone downloads none of it.
- off screen: an `IntersectionObserver` loads it only when it is visible and
  pauses it when it scrolls away. A signed-in reader never loads it at all,
  because the door is not on their screen.

It is `aria-hidden`, `pointer-events: none`, and untabbable. The scrim over it
is a linear fade to `--bg` on all four edges, so the copy keeps its contrast in
the weakest theme rather than being a colour guess per theme.

## Why the server learned ranges

Browsers decide whether they can seek a video by whether the server answers a
`Range` request with `206`. Without it Chrome plays a small clip but loops it
badly and Safari will not start one at all, and neither failure shows up as an
error anywhere. `_byte_range()` in `host/app.py` handles a single range and
falls back to the whole file for anything it cannot answer honestly: a body
shorter than what the browser asked for is read as a corrupt clip, so a
multi-range request gets 200 with the full file instead of one slice of it.

## Tests

- `tests/test_hearth.py`: 404 when absent, 200 + `video/mp4` + `Accept-Ranges`
  when present, exact slices for closed, open-ended, suffix, and the
  whole-file fallback for nonsense ranges, and the render's `data-has` /
  `poster` flags. Public without a session.
- `tests_ui/test_hearth.py`: the built page requests no clip that is not there,
  the fire is inside the welcome pane and outside the form, a phone gets
  `display: none`, and it stays out of the accessibility tree.