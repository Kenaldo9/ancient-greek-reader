# The reader

How the reader works, screen by screen. To run it, see the
[README](../README.md#running-it-yourself); [greek web](cli.md#greek-web)
serves it at http://127.0.0.1:8000.

## Library and navigation

Library cards show the first line, what the text has, the books, and **Start
reading**, which reads **Continue at …** once you have read the text in this
browser. A label says what the text has: **Complete** (every book has
translations), **Books 1–3 with translations**, or **Grammar and dictionary ·
translations coming**. The book chips show the same per book (tinted and
underlined: translations, glosses and notes; half underlined: some), with a
key. They are counted from the saved study data, so a book switches as soon
as its batch is collected. The **About** page (in the header, and in the iPad
drawer) says what each text has, where its data comes from, the licences,
where to report a mistake, and what a visitor's browser keeps.
The
reading position is saved per text in the browser (`localStorage`), so each
device keeps its own; nothing is saved on the server. Book links and the TOC
open the book at its start. The bottom of the Library page (and of the iPad
drawer) shows which code is running and which database it uses (see
[Versions](cli.md#versions)).

The reader shows a whole book that you scroll through; a prose book longer than
40,000 characters is split into parts that start at a chapter. **Continue to
…** at the end, and the arrows in the text toolbar, open the next or previous
book (or part). The reader's **Go to** field accepts `1.42` in the current
text, `Iliad 1.42`, `Odyssey 5` for book 5, or a text title; the line opens at
the top of the text.

Your place follows the scroll. The toolbar shows the line at the top of the
text (`5.412 / 5.909`), and a second after you stop scrolling that line
becomes the reading position and the URL's `at=`, so a reload, a copied link,
Back and Forward, the Library's **Continue at …**, the drawer's text links and
the search's **Back to …** return to it. A text opened without a line (Go to
`Odyssey`) continues there.

## Selection, words, and passage study

- Mouse: click a line number to select it, then Shift+click another to extend.
- Touch: tap the first line number, then the last; tap the same number again
  to clear. Ranges can span books.
- A selection has at most 1,000 lines, more than the longest page (Iliad 5,
  909 lines).
- Click a word for its parsing, lemma, and the study data's meaning and
  note.
- For a text with treebank data (the Iliad, the Odyssey, Herodotus and
  Thucydides), the word pane also shows the treebank's parsing, LSJ's
  definition, the word's role in its sentence and how often its lemma
  occurs ("12× in the Iliad · 2× in Book 1"). Herodotus and Thucydides use
  GLAUx, where some sentences were annotated by its parser rather than by
  hand: their parsing is labelled "GLAUx, automatic" and their roles "may be
  wrong". With study data (the whole Iliad; the other texts follow), it
  also shows:
  - the lemma's dictionary form ("from μῆνις, -ιος, ἡ");
  - a **Syntax** block: the word's role in its treebank sentence ("object
    of ἄειδε"; hovering the word named highlights it in the text) and its
    **Use** ("dative of disadvantage"). On a laptop or iPad, Parsing and
    Syntax are tabs;
  - in the Dictionary column: LSJ's definition, the verb's principal parts
    with the one this form comes from marked, and its **Construction**
    ("+ dat. of the person, be angry with").
- The passage box shows the study data's **Fluent** and **Literal**
  translations and **Grammar (N)**. Lines without study data show Grammar
  only (the treebank's words). In a book without any study data, Fluent and
  Literal say its translations are not yet available (naming the text, or
  the book when others have them), and so does the word pane in place of a
  gloss and note; parsing, roles, LSJ, frequency and Structure work as
  everywhere.
- On a laptop or a monitor (wider than 1500px), **Expand** in the passage
  box's header makes the box fill the study column (on a monitor it closes
  the open LSJ entry). The word folds into a line or two above it (the word,
  its meaning and its parsing), and words you click show there. **Show
  word**, **Collapse** or Escape bring the word pane back. The browser
  remembers the choice.
- For a text with a treebank, **Grammar** has two views, and remembers the
  one you chose; the switch between them stays at the top as the words
  scroll (a phone in landscape keeps only the column heads there). **In
  order** is the table of words with each word's role and saved use.
  **Structure** shows every treebank sentence the selection touches, whole
  (words outside the selection in grey): its text, then an
  outline with one row per word or phrase, indented under the word it depends
  on, with its meaning (LSJ's in grey), a short role and the use. Clicking a
  row opens the word; hovering or focusing it highlights the word and the
  words under it in the text. A row with three rows or more under it folds
  (▾); from the keyboard the outline is a tree (arrow keys, Home/End, Enter).
  A sentence GLAUx's parser annotated says "roles may be wrong". On iPad
  portrait, **Show the sentence** unfolds a sentence's text. While the same
  lines stay selected, each view keeps its place: after the Word tab, another
  tab or the LSJ entry, Grammar opens where you left it.

Turning **Interlinear** on shows the study data's gloss under each word;
lines without study data show the Greek alone. In a book without study data
Interlinear is off and says "No glosses yet"; changing books brings the
book's own control. The CLI `read --interlinear` shows the same glosses. The
Interlinear setting is a browser cookie.
**Grammar** names its sources: the study data's meaning or LSJ's short
definition (in grey), and the Perseus or GLAUx treebank's parsing or, for a
word it lacks, a model's.

## Search

Use the magnifier in the text toolbar to search. Results replace the lines,
and are grouped by book. [greek search](cli.md#greek-search) describes the shared
query syntax and normalization.
Click a result to open its book at that line. Search does not move the saved
reading position until a result is opened; **Back to …** returns to the line
you were reading.

## Layouts

| Viewport width | Reader layout |
|---|---|
| Above 2100px | Navigation, text, three-column word strip above passage box |
| 1501–2100px | 260px navigation, flexible text, 560px study column with compact word card |
| 1101–1500px | Navigation drawer, text, 540px study panel with Word / Fluent / Literal / Grammar tabs |
| 701–1100px | Full-width text, study panel below (540px high, capped at half the viewport height) |
| 700px and below, or 1100px and below and 500px high or less | Phone: full-width text; the study panel is a sheet that opens when a word or line number is tapped |

Reader panes scroll independently. On iPad, a word click opens the Word tab.
The navigation drawer closes with Escape, its backdrop, or a book link.

On iPad (701–1500px, both orientations) the reader has one 60px bar: the
drawer button, the text toolbar (book, range, arrows, selection, search) and
Interlinear. **Go to** and the Library link are in the drawer; tapping the
range in the bar opens it at Go to. The boxes stay where they are while you
read: the page doesn't bounce under a finger, and the range hint floats over
the text instead of resizing it. Added to the Home Screen, the
app is called Greek Reader and shows the owl on teal; iPadOS keeps the icon
from when it was added, so add it again after the icon changes. Its manifest
(`static/manifest.webmanifest`) makes the whole site the app and opens it at
the Library, so no page opens under an in-app browser bar, whichever page it
was added from; a Home Screen app added before the manifest needs adding
again.

On a phone the text takes the screen under one 56px bar: the drawer button,
the book's title over the line at the top (tap it for Go to), the arrows and
search. **Go to** and **Interlinear** are in the drawer. Tapping a word or a
line number opens the study panel as a sheet over the bottom of the screen,
with the same tabs as on an iPad, and brings the word or line up above it.
Its handle, a swipe down on the handle or the tabs, or Escape closes it and
lets go of the word and the lines. The Greek keeps its natural spacing (each
word's target reaches into the space around it), so more lines fit. A phone
in landscape (a screen 500px high or less) has the iPad's bar and the same
sheet, over half the screen.

## Routes

Every route reads saved data (GET); none writes or calls a model.

| Route | Description |
|---|---|
| `/library` | Text cards, with what each text and book has |
| `/about` | What the reader is, what each text has, sources and licences |
| `/version` | The code's commit and the database's version, as JSON |
| `/goto?q=Iliad+1.42` | Jump to a text/reference |
| `/texts/{id}/read` | Reader; `?at=1.42` opens that book at the line, `?lines=1.1-1.7` selects, `?q=μηνιν` searches |
| `/texts/{id}/toc` | Table of contents |
| `/texts/{id}/search` | Compatibility redirect to reader search |
| `/texts/{id}/passage` | Passage box fragment (GET with `lines`) |
| `/texts/{id}/word` | Word fragment (GET with `line`, zero-based token `i`) |
| `/robots.txt`, `/sitemap.xml` | What search engines may index: the Library, About, each text's contents and each page of its books (below) |

## On the open internet

- Search engines may index the Library, About, each text's contents and each
  page of its books, at the URL the Library and the contents open it with
  (`/texts/12/read?at=2.1`; each part of a long book has its own).
  `robots.txt` allows exactly those, and keeps crawlers out of the word,
  passage and search fragments, Go to, and reader URLs opened partway or
  with other settings; `sitemap.xml` lists the same pages. A reader page
  names its page's start as its canonical URL however it was opened. Both
  make whole URLs from the host asked for, with the scheme from
  `X-Forwarded-Proto` behind a proxy such as Cloudflare.
- Every response has a Content-Security-Policy that allows the app's own
  scripts, styles, fonts and images only (no inline code or `style`
  attributes; htmx's injected style is off) and refuses framing, with
  `X-Content-Type-Options: nosniff` and `Referrer-Policy: same-origin`.
- A static file's URL with its current version (`?v=`) may be kept for a
  year (`immutable`), a font for a week; anything else is asked for again (a
  304 when unchanged). Pages have no cache lifetime, so Cloudflare doesn't
  keep them and a new publish shows at once.
- FastAPI's `/docs`, `/redoc` and `/openapi.json` are off.
- The app can serve a read-only database file, as the public site does: it
  reads it in place, without WAL. One that lacks a migration of the code is
  left as it is, and every page says so until a migrated copy takes its
  place (`greek migrate`; [deploy/README.md](../deploy/README.md)).
