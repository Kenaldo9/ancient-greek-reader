# The data

The reader reads one SQLite database, `greek.db`. This page says what is in
it, how it was made, and where to get it. Its licences are in
[DATA-LICENSE.md](../DATA-LICENSE.md), by source and by table; that file is
the one that counts.

## The data release

The database is published as the assets of a GitHub Release of this
repository (tag `data-YYYY-MM-DD`), not in git:

| File | |
|---|---|
| `greek.db.xz` | The database, compressed with xz (about 33 MB; 195 MB as `greek.db`) |
| `SHA256SUMS` | Its checksum: `sha256sum -c SHA256SUMS` |
| `manifest.json` | Its last migration, the code's commit it was exported with, each text (its treebank and licence, its books with translations), and the rows in each table |
| `DATA-LICENSE.md` | The licences |

[greek export-data](cli.md#greek-export-data) makes these files. A release
is the live database without the study batches' bookkeeping (what each
request asked for, its usage and its cost). The [README](../README.md#running-it-yourself)
says how to download it and run the reader on it. A release is at the
migration it was exported at; newer code migrates it when it starts, after
copying it beside itself ([Database upgrades](cli.md#database-upgrades)).

## What is in it

Four texts: Homer's *Iliad* and *Odyssey*, Herodotus' *Histories* and
Thucydides' *History of the Peloponnesian War*.

| Tables | What they hold | Where it comes from |
|---|---|---|
| `texts`, `lines` | The texts: one row per verse line, or per section of prose, with its reference (`1.42`, `1.1.1`) and a sequential line number | The Perseus Digital Library's TEI files ([canonical-greekLit](https://github.com/PerseusDL/canonical-greekLit)), by `greek fetch`; editorial matter left out, and brought up to date in place by `greek update-text` |
| `lines_fts` | The search index of an accent-free copy of each line | Made from `lines` by triggers |
| `tb_sources`, `tb_sentences`, `tb_nodes` | Which treebank each text uses, and every sentence and node of it (punctuation and elliptic nodes included), with whether a sentence was annotated by hand or by a parser | The [Ancient Greek Dependency Treebank 2.1](https://github.com/PerseusDL/treebank_data) for the Iliad and the Odyssey; [GLAUx](https://github.com/alekkeersmaekers/glaux) for Herodotus and Thucydides; by `greek study import-treebank` |
| `word_parses` | Each word's lemma and parse, linked to its treebank node | The treebank (`source = 'treebank'`), or, for a word the treebank lacks, a model (`'model'`) |
| `lexicon_entries`, `lemma_entries` | LSJ's entries, and each lemma's entry and how it was matched (or, when LSJ lacks the lemma, a model's short identification) | Liddell, Scott and Jones, *A Greek-English Lexicon* ([Perseus lexica](https://github.com/PerseusDL/lexica)), by `greek study import-lsj` |
| `word_study` | A gloss and a note on each word, and the model's remark where it disagrees with the treebank's parse (`parse_check`) | Claude Opus 5.5 (below) |
| `line_translations` | A literal translation of each line | Claude Opus 5.5 |
| `translation_units` | A fluent translation of each passage (a translation unit) | Claude Opus 5.5 |
| `word_uses` | How a word works in its sentence ("dative of disadvantage") | Claude Opus 5.5 |
| `lemma_forms` | A noun's or adjective's dictionary form ("μῆνις, -ιος, ἡ"), or a verb's construction ("+ dat. of the person"), per dialect | Claude Opus 5.5 |
| `verb_parts` | A verb's principal parts, per dialect | Claude Opus 5.5 |
| `study_batches`, `study_requests`, `lemma_requests`, `parts_requests` | The batches' bookkeeping | Empty in a release |
| `_migrations` | The migrations applied: the database's version | |

Every row of model data names its model (`model_used`). Deleting a text
deletes its lines, its treebank data and its study data; dictionary forms
and principal parts are kept by lemma. [ARCHITECTURE.md](../ARCHITECTURE.md)
has the details.

## What each text has

| Text | Parsing and roles | Glosses, notes and translations |
|---|---|---|
| Iliad | The Perseus treebank, by hand | Every book |
| Odyssey | The Perseus treebank, by hand | Not yet |
| Histories (Herodotus) | GLAUx: part by hand (from the PROIEL treebank), the rest by GLAUx's parser | Not yet |
| History of the Peloponnesian War (Thucydides) | GLAUx: part by hand (from Vanessa Gorman's treebanks), the rest by GLAUx's parser | Not yet |

The reader's Library labels each text and book from the database itself, so
a new release with more study data shows it with no code change. `greek
study coverage TEXT_ID` reports what each book has.

## How the study data was made

The glosses, notes, translations, use labels, dictionary forms,
constructions and principal parts were written by Claude Opus 5.5,
Anthropic's model, with `greek study`, once, in Message Batches; the reader
itself asks no model anything.

- **Translation units.** A text is cut into units of whole treebank
  sentences, up to 10 lines of verse, or of whole sections of prose within
  a chapter, up to 80 words. One request per unit gives the model the
  unit's lines, then each word numbered with its treebank lemma and parse.
  It answers with a gloss and a note on every word, a literal
  translation of every line, a fluent translation of the unit, and a remark
  wherever it disagrees with the treebank's parse. Each work has its own
  prompts, in `greek/api/works.py` (verse for Homer, prose for Herodotus and
  Thucydides).
- **Checks.** Answers are checked when a batch is collected: every word,
  line and unit must be answered, and a unit that fails is asked again (with
  `--repeat-words`, each word's answer must repeat its word, so a skipped
  word fails where it happens). `greek study coverage` lists what is
  missing, and `greek study remarks` gathers the model's remarks on the
  treebank for review.
- **Use labels** are asked per unit, with the unit's saved glosses and the
  treebank's tree. **Dictionary forms, constructions and principal parts**
  are asked per lemma, per dialect: Homeric for the Iliad and the Odyssey
  (which share them), Ionic for Herodotus, Attic for Thucydides.
- **Settings.** The Iliad's glosses, notes and translations were made at
  effort `high` (Opus 5.5 always thinks before answering), its dictionary
  forms and principal parts at `medium`.

**They can be wrong.** Samples were reviewed while they were made, but not
every word: read them as a study aid, and check them against the parsing and
the dictionary. Where the model disagreed with the treebank, the word pane
shows its remark under Parse check.

## Licences

In short (the details, and how to leave out the non-commercial part, are in
[DATA-LICENSE.md](../DATA-LICENSE.md)):

- The Greek texts and LSJ: CC BY-SA 4.0 (Perseus Digital Library).
- The Iliad's and the Odyssey's treebank: CC BY-SA 3.0 US (Perseus).
- Herodotus' and Thucydides' treebank: GLAUx, CC BY-SA 4.0, except its
  sentences annotated by hand, from PROIEL (CC BY-NC-SA 3.0) and Vanessa
  Gorman's treebanks (CC BY-NC-SA 4.0): those are for non-commercial use.
- The study data: CC BY-SA 4.0.
