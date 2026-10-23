# The command line

Examples use `greek` as shorthand for `python greek.py` locally or
`docker compose run --rm greek` in Docker. In the published database the
texts' IDs are 12 (the Iliad), 13 (the Odyssey), 15 (Thucydides) and 16
(Herodotus); `greek library` lists them.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | For `greek study` batches only |
| `GREEK_DATA_DIR` | `./data` | Directory for database and imported texts |

## Data layout

```
data/
├── greek.db          # SQLite database (the data release's greek.db.xz, decompressed)
├── catalog.json      # Cached Perseus text catalog (greek fetch)
├── sources/          # Treebank and LSJ files (greek study import-treebank, import-lsj)
└── texts/            # Imported text files (tab-separated reference\tcontent)
```

The reader needs only `greek.db`.

## Line reference formats

| Text type | Format | Example |
|---|---|---|
| Poetry (Homer) | `book.line` | `1.1-1.10` or `1.1-10` |
| Prose (Herodotus) | `book.chapter.section` | `1.1.1-1.1.5` |
| Prose (Thucydides) | `book.chapter.section` | `1.1.1-1.2.3` |

Integer ranges (`1-10`) always work as a fallback. Use `greek toc TEXT_ID` to discover the exact reference structure of any text.

## Commands

`greek --version` shows the code's commit and the database's version without
opening the database for writing (see [Versions](#versions)).

### greek library

List all imported texts in a Rich table.

#### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--sort`, `-s` | choice | `id` | Sort field: `id`, `title`, `author`, or `imported_at` |
| `--author`, `-a` | text | — | Filter results to a specific author |
| `--short` | flag | off | Print a compact one-line-per-text list (ID, title, author) |

#### Examples

```
greek library
greek library --sort title
greek library --author Homer
greek library --short
```

---

### greek read

Read lines from an imported text. Without `--lines`, output is paginated.

#### Arguments

| Argument | Description |
|----------|-------------|
| `TEXT_ID` | Integer ID of the text (see `greek library`) |

#### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--lines`, `-l` | text | — | Line range to display: `1-20` or `1.1-1.20` |
| `--page-size` | int | `40` | Lines per page when paginating (no `--lines` given) |
| `--interlinear`, `-i` | flag | off | Show the study data's gloss under each word; lines without study data show `·` |

#### Examples

```
greek read 12 --lines 1.1-1.10
greek read 12 --lines 1.1-1.10 --interlinear
greek read 16 --lines 1.1.1-1.1.5
greek read 12 --page-size 20
```

---

### greek search

Search for a word or phrase within a specific text using FTS5. Accents,
breathings, capitals, and final sigma differences are ignored. Matching lines
are highlighted; surrounding context lines are shown dimmed. Web and CLI
search use the same normalization.

#### Arguments

| Argument | Description |
|----------|-------------|
| `TEXT_ID` | Integer ID of the text to search |
| `QUERY` | Search term (polytonic Greek or English) |

#### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--context`, `-c` | int | `2` | Number of lines to show before and after each match |
| `--limit`, `-n` | int | `20` | Maximum number of matching lines to return |

#### Examples

```
greek search 12 μῆνιν
greek search 12 μηνιν
greek search 12 "μῆνιν ἄειδε"
greek search 12 "μην*"
greek search 12 "Ἀχιλλῆος" --context 3
greek search 16 Κροῖσος --limit 50
```

---

### greek import

Import a local `.txt` file into the library. The file may be plain text (one line per line) or tab-separated `reference\tcontent` format (detected automatically).

#### Arguments

| Argument | Description |
|----------|-------------|
| `FILE` | Path to the `.txt` file to import |

#### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--title`, `-t` | text | (required) | Title to store for this text |
| `--author`, `-a` | text | — | Author name (optional) |

#### Examples

```
greek import iliad.txt --title "Iliad" --author Homer
greek import my_text.txt -t "Fragments" -a "Anonymous"
```

---

### greek fetch

Search the Perseus Digital Library catalog and download Greek texts directly. On first use, builds a local catalog (~1 minute, cached in `data/catalog.json`).

#### Arguments

| Argument | Description |
|----------|-------------|
| `QUERY` | Search term (author name, title keyword, etc.) — optional |

#### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--list` | flag | off | Show all available texts in the catalog without searching |
| `--refresh` | flag | off | Rebuild the catalog by re-fetching from Perseus GitHub |
| `--all` | flag | off | Download all matching results without prompting |
| `--force` | flag | off | Re-import even if a text is already present (replaces existing data) |

#### Examples

```
greek fetch Homer
greek fetch "Peloponnesian War"
greek fetch --list
greek fetch --refresh
greek fetch Plato --all
greek fetch Homer --force
```

---

### greek update-text

Update a fetched text's lines in place from its current Perseus file: Perseus's
corrections since the import, and editorial matter the parser now leaves out
(footnote markers, citations of quoted passages). It shows each changed line's
removed and added words and asks before updating. Line numbers and references
never change, so readers' saved places stay right.

Nothing is changed when the file's references differ from the saved text's, or
when a changed line has saved data tied to its words (the study data's
glosses, notes and translations, use labels, a model's parses). Afterwards, run `greek study
import-treebank` again for a text with a treebank. Back up the database first.

#### Arguments

| Argument | Description |
|----------|-------------|
| `TEXT_ID` | ID of the text (see `greek library`) |

#### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--file` | path | — | Read a local TEI file instead of downloading the text's Perseus file |
| `--yes`, `-y` | flag | off | Update without asking |

#### Examples

```
greek update-text 16
greek update-text 15 --file tlg0003.tlg001.perseus-grc2.xml
```

---

### greek toc

Show the table of contents for an imported text: books, section counts, first and last references, and the corresponding integer line range.

#### Arguments

| Argument | Description |
|----------|-------------|
| `TEXT_ID` | Integer ID of the text |

#### Examples

```
greek toc 12
greek toc 16
```

---

### greek delete

Delete an imported text by ID, removing its lines, study data and the
underlying `.txt` file. Prompts for confirmation.

#### Arguments

| Argument | Description |
|----------|-------------|
| `TEXT_ID` | Integer ID of the text to delete |

#### Examples

```
greek delete 17   # a text you imported
```

---

### greek study

Study data for a text with a treebank: the treebank's parse and tree for
every word, LSJ entries, and model data made in Message Batches at half the
standard price. The Iliad and the Odyssey use the Perseus treebank
(`--work iliad`, `--work odyssey`); Herodotus and Thucydides use the GLAUx
corpus (`--work herodotus`, `--work thucydides`). Each work has its own
prompts: verse units are whole sentences up to 10 lines, prose units whole
sections within a chapter up to 80 words. Dictionary forms, constructions and
principal parts are kept per dialect (Homeric for the Iliad and the Odyssey,
which share them; Ionic for Herodotus; Attic for Thucydides). Only `submit
--yes` spends money: run `estimate` first and approve its cost. `greek study
<command> --help` lists each command's options.

| Command | What it does |
|---|---|
| `import-treebank`, `import-lsj` | Align a treebank with a text (every node, for word roles) and match its lemmas to LSJ. Free. `--nodes-only` also records the treebank of a text imported before migration 018. |
| `batch estimate \| submit` | A gloss and note for every word, literal and fluent translations, one request per translation unit. `estimate --show` prints the requests; `--repeat-words` has each word's answer repeat its word, checked on collect. |
| `notes`, `parts` | Notes only; principal parts of the text's verbs. |
| `forms estimate \| submit` | Dictionary forms of nouns and adjectives, and the constructions of verbs, per lemma. |
| `uses estimate \| submit` | How words work in their sentence (genitives, datives, participles, moods), per translation unit; it needs the unit's saved study data. |
| `batch status \| collect` | Follow any of these batches and store its results. |
| `coverage` | What the saved study data covers, book by book (words glossed, literal lines, units, use labels, remarks), the dictionary forms, principal parts and LSJ entries, and the first missing items. Free. |
| `remarks` | The model's `parse_check` remarks on the treebank's parses, as a Markdown report for review (`--books`, `--out`; default `remarks-<id>.md` in the data directory). Free. |

#### Examples

```bash
greek study import-treebank 14 --work herodotus
greek study forms estimate 12 --books 2-7
greek study uses submit 12 --books 2-7 --effort high --yes
greek study batch status
greek study batch collect msgbatch_...
greek study coverage 12
greek study remarks 12 --books 8-24
```

### greek shell

Start an interactive REPL session. All commands are available without re-launching the container. Tab-completion is enabled and command history persists across sessions in `data/.greek_history`.

Type `help` or `?` for usage, `exit` or Ctrl-D to quit.

#### Examples

```
greek shell

greek› library --short
greek› toc 12
greek› read 12 --lines 1.1-1.10 --interlinear
greek› exit
```

---

### greek web

Start the web interface, powered by FastAPI, Jinja2, HTMX, and vanilla JavaScript.
The server shares the CLI's database and only reads it.
If the Docker `web` service is running, stop it first (`docker compose stop web`).

#### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--host` | text | `127.0.0.1` | Bind host |
| `--port` | int | `8000` | Bind port |
| `--reload` | flag | off | Enable auto-reload (development) |

```bash
greek web
greek web --port 8080
greek web --host 0.0.0.0 --port 8000
greek web --reload
```

### greek backup

Copy the database to a new file, or with `-` to standard output: a
consistent copy made with SQLite's backup API while the web app keeps
running, checked with `integrity_check`. It never overwrites a file.

```bash
greek backup /tmp/greek-copy.db
ssh HOST docker exec CONTAINER python greek.py backup - > greek.db
```

### greek export-data

Write the database as it is published for others (a GitHub Release's
assets) to a new or empty folder. It is a copy made as `greek backup` makes
it, without the study batches' bookkeeping (`study_batches` and their
requests: what each asked for, its usage and its cost; the study data they
saved stays). The copy is vacuumed, checked again (`integrity_check`,
`foreign_key_check`) and compressed with xz, and the command checks that it
decompresses to the same bytes. The web app keeps running meanwhile. The
whole database takes about a minute on a laptop, and longer on a Raspberry
Pi. If it fails, it removes what it wrote, and it never overwrites a file.

| File | |
|---|---|
| `greek.db.xz` | The database: `xz -d greek.db.xz` gives `greek.db` for `data/` |
| `SHA256SUMS` | Its checksum: `sha256sum -c SHA256SUMS` |
| `manifest.json` | Its last migration, the code's commit, each text (its treebank and licence, its books with translations), the rows in each table, and the rows emptied |
| `DATA-LICENSE.md` | Each part of the data's licence ([DATA-LICENSE.md](../DATA-LICENSE.md)) |

```bash
greek export-data /tmp/greek-export
ssh HOST docker exec CONTAINER python greek.py export-data /data/export
```

### greek migrate

Apply the database's pending migrations and say where it stands. Every
command does this as it starts (see below); this one does nothing else, for
a database the web app serves read-only (the public site's published copy,
[deploy/README.md](../deploy/README.md)) or one from a data release made by
older code.

```bash
greek migrate
```

## Database upgrades

The CLI and web app apply pending SQL migrations at startup. Before they
change an existing database, they copy it beside itself, e.g.
`data/greek-backup-2026-10-13-pre-020_example.db`, with SQLite's backup API
(committed WAL data included) and check the copy; a second copy the same day
gets `-2`. If the backup fails, nothing is migrated. A command prints where the
backup went; the web app writes it to its log (`docker compose logs web`).
Old backups are never deleted: remove them yourself when you no longer need
them.

A database that has migrations the code doesn't have was upgraded by newer
code. Commands then stop with an error, and the web app shows that on every
page (`/version` still answers), until you update the checkout or rebuild the
image. Nothing is migrated.

A read-only database file (one the app can't write, such as the public
site's on a read-only mount) is read where it is, without WAL. If it lacks
some of the code's migrations, commands stop and the web app says so on every
page in the same way: migrate a writable copy with `greek migrate` and put
it in its place.

Back up by hand before other bulk changes, such as `import-treebank`, with
[greek backup](#greek-backup):

```bash
greek backup data/greek-before-import.db
```

Migration `009_search_plain.sql` adds an accent-free FTS index.
Migration `010_delete_orphan_lines.sql`
removes lines whose parent text no longer exists, keeping the search index
synchronized. See [ARCHITECTURE.md](../ARCHITECTURE.md) for implementation
details.

## Versions

The code's version is the git commit it is from; the database's is the last
migration it has had (the files in `greek/db/migrations/`). There is no
separate number to raise by hand.

```console
$ docker compose run --rm greek --version
code      1a2b3c4 (built 2026-10-16 10:49)
database  020_reading_app (/data/greek.db)
```

A checkout reads its commit from `.git` (uncommitted changes aren't shown); the
Docker image records it when it is built, with the build time. The database
line also says when the next start will apply migrations, or when the database
is newer than the code. The same appears at the bottom of the Library page and
the iPad drawer (`Code 1a2b3c4 · built 16 Oct 2026 10:49 · Database 020`), and
as JSON at `/version`.
