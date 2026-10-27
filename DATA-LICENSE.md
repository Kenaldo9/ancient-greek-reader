# Data licences

The Ancient Greek Reader's database, `greek.db` (exported as `greek.db.xz`),
brings together data from several sources, and each part keeps its own
licence. They are listed below with the tables that hold them. The reader's
code is under the MIT licence (`LICENSE`); this file covers the data. Each
licence was checked against its source's licence file on 2026-10-16.

## The Greek texts

`texts`, `lines` (and the search index made from them, `lines_fts`).

The Perseus Digital Library, Tufts University,
[canonical-greekLit](https://github.com/PerseusDL/canonical-greekLit):
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Herodotus'
*Histories* (`tlg0016.tlg001.perseus-grc2`) and Thucydides' *History of the
Peloponnesian War* (`tlg0003.tlg001.perseus-grc2`) state it in their headers;
the *Iliad* and the *Odyssey* (`tlg0012.tlg001` and `tlg0012.tlg002`,
`perseus-grc2`) state none of their own and come under the repository's.

## Parsing and syntax

`word_parses` (its rows whose `source` is `treebank`), `tb_nodes`,
`tb_sentences` and `tb_sources`. Every row has its text's `text_id`, and
`tb_sources` names each text's treebank and licence.

- **The Iliad and the Odyssey:** the Ancient Greek Dependency Treebank 2.1,
  Perseus Digital Library,
  [treebank_data](https://github.com/PerseusDL/treebank_data):
  [CC BY-SA 3.0 United States](https://creativecommons.org/licenses/by-sa/3.0/us/).
- **Herodotus and Thucydides:** the
  [GLAUx corpus](https://github.com/alekkeersmaekers/glaux) (Alek
  Keersmaekers, KU Leuven):
  [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), except
  the sentences annotated by hand (`tb_sentences.annotation = 'manual'`),
  which GLAUx took from other treebanks:
  - Herodotus: the [PROIEL treebank](https://github.com/proiel/proiel-treebank)
    (Dag Haug and others):
    [CC BY-NC-SA 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/us/).
  - Thucydides: Vanessa Gorman's
    [Ancient Greek prose dependency treebanks](https://github.com/vgorman1/Greek-Dependency-Trees):
    [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/),
    as its README and GLAUx state (the licence file beside that README
    holds CC BY-SA 4.0's text; the stricter one is followed here).

  These two are for non-commercial use only, and can't be relicensed under
  CC BY-SA. Without them, the rest is CC BY-SA: leave out the rows of
  `tb_sentences` with `annotation = 'manual'` for these two texts, and the
  rows of `tb_nodes` and `word_parses` with the same `text_id` and
  `sentence_id`. The sentences annotated by GLAUx's parser
  (`annotation = 'auto'`) are CC BY-SA 4.0.

## The dictionary

`lexicon_entries`: Liddell, Scott and Jones, *A Greek-English Lexicon*,
Perseus Digital Library, [lexica](https://github.com/PerseusDL/lexica):
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
`lemma_entries` matches each lemma to its entry.

## Glosses, notes, translations and word details

`word_study`, `line_translations`, `translation_units`, `word_uses`,
`lemma_forms`, `verb_parts`, `lemma_entries.model_def`, and the rows of
`word_parses` whose `source` is `model`. Claude Opus 5.5, Anthropic's
model, wrote them in batches from the Greek text and the treebank's parse of
each word; `model_used` names the model. **They can be wrong.**

So far they cover the Iliad, made from the CC BY-SA sources above:
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), Kenaldo9
(Ancient Greek Reader). Herodotus and Thucydides have none yet; theirs would
be made from their GLAUx annotation, and their licence will be stated here
when they are published.

## Using the data

Credit the sources of the parts you use, as above, and share what you make
from them under the same licence (the ShareAlike terms). Each licence's own
text has its terms and its disclaimer of warranties: the data is provided as
is.
