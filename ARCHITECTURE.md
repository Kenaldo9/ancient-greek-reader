# Architecture

Ancient Greek Reader is a reading app with a server-rendered FastAPI web
interface and a Click CLI. Both use the same SQLite database, repository
functions and dataclasses. The web app only reads: it makes no model calls
and keeps no one user's state (the reading position is in the browser).
Model data is made by the CLI's `greek study` in Message Batches. There is
no ORM or frontend build step.

Read this file before changing code or behavior. The reader's behavior is
described in [docs/reader.md](docs/reader.md), the CLI in
[docs/cli.md](docs/cli.md), and the database's tables, how its data was made
and its licences in [docs/data.md](docs/data.md).

## Layers and entry points

| Location | Responsibility |
|---|---|
| `greek.py`, `greek/cli/` | Load `.env`, run migrations, register and execute Click commands; `--version` |
| `greek/db/connection.py` | Open SQLite connections and apply numbered SQL migrations, backing up an existing database first |
| `greek/db/export.py` | The database's public export (`greek export-data`) for a GitHub Release |
| `greek/version.py` | The code's commit (the Docker image's `build.json`, or `.git`) and the database's last migration |
| `greek/db/repos/` | SQL data access shared by the CLI and web routes |
| `greek/models/` | Dataclasses representing stored texts, treebank data, study data, and verbs |
| `greek/api/` | Study batches: each work's prompts (`works.py`), requests and results (`study.py`, `parts.py`, `forms.py`, `uses.py`), and models' display names |
| `greek/fetch/` | Perseus catalog, downloads, and TEI parsing for poetry and prose |
| `greek/display/` | Rich CLI rendering |
| `greek/search.py` | Greek normalization, FTS query construction, and match highlighting |
| `greek/web/app.py` | Load `.env`, apply migrations at import (or, for a database newer than the code or read-only and behind it, answer every page with why), mount static files with their cache lifetimes, register routers, gzip pages, fragments, CSS and JS (level 6: a book page of about 690 KB is sent as 50–70 KB), add the security headers (a Content-Security-Policy for the app's own files only), and serve `/version`; no API docs |
| `greek/web/routes/site.py` | `robots.txt` and `sitemap.xml`: the pages search engines may index, from `page_starts` in `reader_data.py` |
| `greek/web/deps.py` | Request-scoped database connections |
| `greek/web/reader_data.py`, `reading.py`, `formatting.py` | Prepare reader views, paginate books, map saved words to tokens, and format display values |
| `deploy/`, `.github/workflows/deploy.yml` | Tests and a secret scan (gitleaks) on every push, then on `main` a pull, rebuild and checks on a Raspberry Pi, for the private reader and the public site on a published copy of the database (`deploy/public/`, `publish.sh`; [deploy/README.md](deploy/README.md)) |
| `greek/web/templates/`, `static/` | Jinja pages and fragments, HTMX, vanilla reader JS, one app stylesheet, and self-hosted fonts; templates link static files with `static_url`, which adds a content hash (`?v=`) so browsers fetch changed files |

## Persistence

`GREEK_DATA_DIR` selects the data directory (default `./data`). Connections
enable foreign keys and WAL. `run_migrations()` applies ordered `.sql` files
once, tracked by filename in `_migrations`; it registers `greek_plain()` for
the search migration. Before applying any to an existing database file it
copies it to `greek-backup-<date>-pre-<migration>.db` and checks the copy, and
it refuses a database with migrations the code doesn't have
(`DatabaseNewerThanCode`): the CLI exits with the reason, the web app shows it
on every page. The last applied migration is the database's version, and the
git commit is the code's (`greek --version`, `/version`, the Library page and
the iPad drawer). A database file the process can't write (the public
site's published copy, on a read-only mount) is opened read-only and read in
place, without WAL; if it lacks some of the code's migrations,
`run_migrations()` leaves it alone (`DatabaseNeedsMigrations`) and the web
app says so on every page: `greek migrate` migrates a writable copy, which
the deploy then swaps in. `greek backup` writes a copy made the same way as a
migration backup (`copy_database`: SQLite's backup API while the app keeps
running, then `integrity_check`) to a new file or standard output, so a
backup elsewhere can fetch the live database over ssh.

`greek export-data` writes the database as it is published, for running
the reader elsewhere: a copy made the same way, without the study batches'
bookkeeping (`study_batches`, `study_requests`, `lemma_requests` and
`parts_requests`: what each request asked for, its usage and its cost). The
copy is switched out of WAL mode so that it is one file, vacuumed, checked
(`integrity_check` and `foreign_key_check`), and compressed with xz (the
standard library's `lzma`, which gave a smaller file than zstd), and the
command checks that it decompresses to the same bytes. Beside it go
`SHA256SUMS`, `manifest.json` (its last migration, the code's commit, each
text's treebank, licence and translated books, and the rows in each table)
and `DATA-LICENSE.md`, the data's licences by source and table, from the
repository's root, which the Docker image copies in.

- `texts` and `lines` store imported works. A sequential `line_number` is
  separate from the displayed `reference`: poetry uses `book.line`, prose
  often uses `book.chapter.section`. A prose row is a whole section.
- The TEI parser leaves out editorial apparatus (footnote markers, citations
  of quoted passages, a corrected misprint). `greek update-text` brings an
  imported text up to date with its current TEI file in place, by reference;
  it refuses when references would move or when a changed line has saved
  data tied to its words.
- `lines.plain` is an accent-free, lowercase copy with final sigma normalized.
  FTS5 indexes that copy; insert, update, and delete triggers synchronize the
  index. Web and CLI search use the same repository function.
- Migration 020 (the reading app) dropped the tables of one-off model
  answers and of one user's state: `analyses`, `history`,
  `word_translations`, `questions`, `bookmarks` and `reading_positions`, and
  the Verbs page's lookups in `verb_parts` (dialect "").

Study data is made in Message Batches by `greek study` and recorded in
`study_batches` with its requests, usage and cost (the whole Iliad so far;
the published export leaves this bookkeeping out).
`greek study coverage` and `greek study remarks` report on the saved data
without calls: what each book has and lacks, and the model's `parse_check`
remarks on the treebank for review.
Each work's prompts come from its profile in `greek/api/works.py` (keyed by
`tb_sources.work`): the Iliad's are the ones its first books were made with,
byte for byte (a snapshot test); the Odyssey's are the same Homeric prompts;
Herodotus and Thucydides have prose prompts. A translation unit is whole
treebank sentences up to 10 lines of verse, or whole prose sections within a
chapter up to 80 words. A prose request marks the words GLAUx's parser
annotated and leaves editorial marks and speakers' labels out of its numbered
words.
- `word_parses` holds a treebank's lemma and parse for every word, linked to
  its node in `tb_nodes` (every node of every sentence, elliptic and
  punctuation nodes included). `greek/syntax.py` turns a node and its
  sentence into a role phrase ("object of ἄειδε").
- `tb_sources` records which treebank a text uses (`WORKS` in
  `greek/fetch/treebank.py`): the Perseus treebank for the Iliad and the
  Odyssey, GLAUx for Herodotus and Thucydides. `tb_sentences.annotation`
  says whether a sentence was annotated by hand or by GLAUx's parser; the
  word pane credits each word's sentence and marks automatic roles. A text
  without a `tb_sources` row reads as the Perseus treebank.
  `SentenceSyntax.outline()` turns a sentence into nested rows (a preposition
  on its object's row, a subordinating conjunction on its verb's) for the
  passage box's Structure view.
- `lexicon_entries` and `lemma_entries` hold LSJ and its match to each
  lemma.
- `word_study`, `line_translations` and `translation_units` hold the model's
  glosses, notes and translations; `verb_parts` also holds principal parts.
  `study_line_glosses` gives a line's glosses by word position for the web
  interlinear and `greek read --interlinear`. `book_study` counts each
  book's study data by line in SQL (lines with a literal translation, lines
  inside a saved unit, glossed words): a unit's results are saved in one
  transaction, so a book whose every line is in a saved unit is complete as
  `greek study coverage` counts it (a test checks they agree). `TextStudy`
  in `reader_data.py` turns it into the Library's labels and book chips, the
  About page's table, and the reader's notes in a book without study data.
- `lemma_forms` holds a lemma's dictionary form (nouns and adjectives) or
  construction (verbs), keyed by model and dialect (Homeric for the Iliad
  and the Odyssey, which share them, Ionic for Herodotus, Attic for
  Thucydides), as are a batch's principal parts in `verb_parts`; the word
  pane prefers the text's dialect and names another one it falls back to.
  `word_uses` holds a word's use label ("dative of disadvantage"), keyed by
  model.

The word pane reads all of this on GET (`study_word` in `reader_data.py`)
and counts a lemma's frequency from `word_parses`; it makes no model calls.
So does the passage box's Grammar tab: `passage_view` adds each word's
role and use, and the outline of every treebank sentence the words touch.

Deleting a text cascades to its lines, treebank data and study data.
Principal parts and dictionary forms are kept by lemma, independent of texts.

## Web interaction model

The Library links to a reader with navigation, independently scrolling text,
word details, and a passage box. Search replaces the text pane. The responsive
layout uses a compact word card on laptops, a navigation drawer and shared
study tabs on iPad landscape, and a bottom study panel on iPad portrait. On
a laptop one box can fill the study column: the open LSJ entry (the passage
box folds into a bar) or the expanded passage box (the word folds into its
summary; `reader.js` remembers this in `localStorage`). On
iPad, one slim bar replaces the header and the text toolbar: the toolbar keeps
its place in the text pane's markup but is fixed into the bar, and `reader.js`
moves Go to into the drawer (and back above 1500px), keeping its id for the
delegated listeners and the pane links' out-of-band `#goto` swap. Pane links
also swap `#interlinear-control` out of band: in a book without study data
Interlinear is disabled with "No glosses yet", and `reader.js` keeps the
swapped checkbox as the page has it.

A phone (700px and below) has the same slim bar, 56px high, with the book's
title over the range, and `reader.js` moves Interlinear into the drawer too.
On a phone, and on a short screen with the study panel under the text (1100px
and below, 500px high or less: a phone in landscape), the study panel is a
sheet (`#reader.sheet-open`): closed until a word or a line number is tapped,
when the text above it gets shorter and `reader.js` scrolls the word or line
up out from under it. Its handle, a swipe down on its handle or tabs (pointer
events; the click a swipe ends with is swallowed), or Escape closes it and
lets go of the word and the selection, so nothing is selected while it is
closed; a page opened with `lines=` opens with it. There the Greek keeps its
natural spacing, and a word's or line number's target reaches into the space
around it (a pseudo-element) instead of the iPad's 44px boxes.

A page is a whole book; a book longer than 40,000 characters is split into
parts at chapter starts (`greek/web/reading.py`). Opening a page scrolls the
opened line to the top and records it as the text's reading position. The
position then follows the scroll: `reader.js` shows the top line in the
toolbar and, a second after scrolling stops, puts it in the URL's `at=`
(`replaceState`) and saves it, and again on `pagehide`. Reading positions
are this browser's: `positions.js` keeps one per text in `localStorage`
(`greek.position.<text id>`), turns the Library's **Start reading** into
**Continue at …** and points the drawer's text links there; a reader URL
without `at=`, `lines=` or `q=` opens at the start, and `reader.js` then
replaces it with the saved place. Without storage the reader works and
always starts at the beginning. The reader opts out of htmx's history snapshots
(`hx-history="false"`): a book is about a million characters, so Back loads
the page again at the `at=` of the entry it returns to.

Selecting lines fetches `/texts/{id}/passage`; clicking a token fetches
`/texts/{id}/word`. Both read saved data only. In a book without study data
they say its translations, glosses and notes are not yet available, naming
the text (or the book, when other books have them); `/about` names the
sources and licences. The passage fragment holds
both views of Grammar (In order and Structure); `reader.js` switches
them, remembers the choice in `localStorage` as it does the tab, keeps each
view's scroll place while the same lines stay selected, and runs the
outline's tree (folding, the keyboard, lighting a row's words in the text).
The study data is keyed by word position (a line's whitespace tokens).
Shared punctuation stripping lives in `greek/display/analysis.py`.

Every route is a GET that reads saved data; the app has no write routes and
imports no model client, so it needs no API key (tests check both).

On the open internet: `robots.txt` lets search engines index the Library,
About, each text's contents and each page of its books at the URL the
Library and the contents open it with (`?at=` its first reference), and no
other reader URL, fragment or Go to; `sitemap.xml` lists the same pages, and
a reader page names its page's start as its canonical URL. Whole URLs take
the host asked for and the scheme from `X-Forwarded-Proto` (Cloudflare's
tunnel reaches the app over plain HTTP). The Content-Security-Policy allows
the app's own scripts, styles, fonts and images only: templates have no
inline code or `style` attributes (the outline's depth is a `data-depth`
attribute; `base.html` turns off htmx's injected style), and `reader.js`
sets styles through the CSSOM. A static URL with the file's current version
may be kept for a year, a font for a week; pages have no cache lifetime.

## Validation

Tests set `GREEK_DATA_DIR` to a temporary directory before app imports and use
in-memory SQLite for repository/route tests. Model requests must be mocked.
Optional Playwright regressions cover all four target viewports, selection,
tabs, and the drawer, and phones in portrait and landscape in Chromium and
WebKit. See [CLAUDE.md](CLAUDE.md) for commands and the preview
workflow. Reading, searching, and inspecting saved data require no model
calls; browser tests fail on any write request.
