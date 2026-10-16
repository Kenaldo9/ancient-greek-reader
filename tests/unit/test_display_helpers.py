from greek.display.reader import _interlinear_text, _line_glosses
from greek.models.text import Line


def _line(content, number=1):
    return Line(id=number, text_id=1, line_number=number, content=content)


def _interlinear(content, study):
    glosses = _line_glosses(_line(content), study)
    return _interlinear_text(content, glosses, available_width=80, indent="").plain


def test_interlinear_text_shows_each_words_gloss_under_the_greek():
    plain = _interlinear("Μῆνιν ἄειδε", {1: ["the wrath", "sing"]})
    assert "Μῆνιν ἄειδε" in plain
    assert "the wrath" in plain
    assert "sing" in plain


def test_study_glosses_go_by_position():
    # The same form twice gets two glosses
    glosses = _line_glosses(
        _line("ἥμισυ μὲν ἥμισυ"), {1: ["half", "on the one hand", "the other half"]}
    )
    assert glosses == ["half", "on the one hand", "the other half"]


def test_a_line_without_fitting_study_glosses_shows_dots():
    assert _line_glosses(_line("Μῆνιν ἄειδε"), {2: ["x", "y"]}) == ["·", "·"]
    # A study list of another length (the line changed) is not used.
    assert _line_glosses(_line("Μῆνιν ἄειδε"), {1: ["only one"]}) == ["·", "·"]
    assert _line_glosses(_line("Μῆνιν"), {1: [""]}) == ["·"]
