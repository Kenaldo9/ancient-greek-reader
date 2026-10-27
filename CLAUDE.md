# Ancient Greek Reader — development guide

Read [ARCHITECTURE.md](ARCHITECTURE.md) before code, architecture, or behavior
changes. Setup is in [README.md](README.md); the reader's behavior in
[docs/reader.md](docs/reader.md), the CLI in [docs/cli.md](docs/cli.md), and
the database in [docs/data.md](docs/data.md). Personal instructions go in a
gitignored `CLAUDE.local.md`.

## Conventions

- Keep FastAPI + Jinja2 + HTMX: one app stylesheet, self-hosted fonts, small
  vanilla JS, no build step. CLI and Docker stay independent of layout changes.
- Put SQL in `greek/db/repos/`, shared records in `greek/models/`, and reader
  view preparation in `greek/web/reader_data.py` / `reading.py`.
- Display structured references; use sequential line numbers for range queries.
  A prose row may be an entire section.
- Keep model identity in study data and verb keys. Reuse `greek/search.py`
  for accent-insensitive search and highlighting in both interfaces.
- The web app is a reading app: every route is a GET on saved data, it makes
  no model calls (no API key), and it keeps no one user's state on the
  server; reading positions are in the browser (`positions.js`). Model data
  is made only by `greek study` batches. The public site counts its visitors
  in daily totals outside the database (`greek/web/visits.py`, `/visit`):
  never anything that tells one visitor from another.
- Greek typography is Gentium Book Plus (400/700), UI is IBM Plex Sans, and
  references are IBM Plex Mono. Fonts and OFL licenses are under
  `greek/web/static/fonts/`.
- Templates link static files with `static_url()`, which adds a content hash
  so browsers fetch changed files.
- Pages pass a strict Content-Security-Policy (`greek/web/app.py`): no inline
  `<script>` or `<style>`, `style=` or `on…=` attributes; use classes or
  data attributes in the stylesheet, or set styles from JS. Browser tests
  fail on a violation.
- Add numbered SQL migrations; do not rewrite applied migrations.
  `run_migrations()` backs up an existing database before applying new ones
  and refuses one newer than the code (docs/cli.md); back up by hand before
  other bulk changes (`greek backup`). Delete through `lines` so its
  triggers update FTS. Do not hard-code text IDs for cleanup.
- A new table of model data gets its licence in `DATA-LICENSE.md` and its
  row in `docs/data.md`; a table of bookkeeping that should not be published
  goes in `EMPTIED` in `greek/db/export.py`.

## Development workflow

Read a file and relevant callers/dependencies before editing. For new work,
briefly describe the approach first. Test meaningful behaviors and edge
cases, run the full suite, and show actual output before calling work
complete.

There is no package build/install step for source edits: run the current
checkout with the project's virtualenv. Install requirements when
dependencies change.

Model calls cost money. Only `greek study … submit --yes` makes them (run
`estimate` first); never make one for a test or a preview, and ask before
any action that spends money.

## Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -q
```

- `tests/unit/`: parsing, prompts, formatting, paging, search normalization,
  treebank and LSJ imports, the public tunnel's wall probe.
- `tests/integration/`: in-memory SQLite migrations/repositories, cascade
  deletes, FTS, study batches, the data export, routes (read-only), HTMX
  fragments, and the public site's outside check.
- `tests/fixtures/`: mock API output and poetry/prose TEI excerpts.
- `tests/integration/test_web_layouts*.py`: optional Playwright regressions for
  four viewports, selection, tabs, drawer focus/dismissal and the browser's
  reading position; `test_web_layouts_phone.py` runs phones (393×852,
  360×800, 852×393) in Chromium and WebKit. Any write request fails them.

Route tests require `httpx`; browser tests also need Playwright, Chromium and
(for phones) WebKit:

```bash
pip install httpx playwright
playwright install chromium webkit
python -m pytest tests/integration/test_web_layouts.py -q
```

`GREEK_TEST_BROWSER` can select an existing Chromium executable. Browser tests
skip when Playwright or the browser is unavailable. Test bootstrap sets `GREEK_DATA_DIR`
to a temporary directory before app imports and masks API keys; mock the
Message Batches client in study tests. Tests never migrate a real database.
Live model calls and Perseus fetches are outside the suite. Set
`GREEK_DATA_DIR` to a scratch directory before importing the app outside the
suite too: an import creates and migrates `data/greek.db`.

Use `ruff check` / `ruff format --check` on changed Python files,
`node --check greek/web/static/reader.js` when JS changes, and `git diff --check`.

## Visual checks without model calls

A UI change ends with screenshots of the real app. Use a copy of the
reader's own `greek.db` (`greek backup`) in a scratch directory as the
preview database (the whole Iliad has study data) and set `GREEK_DATA_DIR` before importing the
app. The app makes no model calls and has no write routes; still block
non-GET requests in the browser. Show the data as it is: never edit a
text's rows to stage a state.

Capture Firefox screenshots at the approved CSS viewport sizes:

```bash
firefox --headless --no-remote --profile /tmp/greek-preview-profile \
  --window-size=2560,1324 --screenshot /tmp/monitor.png \
  'http://127.0.0.1:8000/texts/12/read?lines=1.1-1.7&interlinear=true'
```

Use a fresh temporary profile per capture. The four viewports are monitor
2560×1324, laptop 1824×1024, iPad landscape 1373×789, and iPad portrait
954×1208; phones are 393×852, 360×800 and 852×393 (tapped states with
Playwright's WebKit and touch). Inspect captures and fix overflow/alignment
before delivery.

## Adding a CLI command

Create `greek/cli/my_cmd.py` with a `@click.command("my-cmd")` function, then
import and register it in `greek.py` with `cli.add_command(my_cmd)`. Reuse
repositories and client functions rather than web route handlers. Document
it in `docs/cli.md`.
