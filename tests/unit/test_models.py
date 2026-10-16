from greek.api.models import model_label


def test_the_study_model_has_its_label():
    from greek.api.study import MODEL

    assert model_label(MODEL) == "Claude Opus 5.5"


def test_other_claude_models_have_labels():
    assert model_label("claude-fable-5-1") == "Claude Fable 5.1"
    assert model_label("claude-opus-4-6") == "Claude Opus 4.6"


def test_an_unknown_model_is_shown_by_its_id():
    assert model_label("some-old-model") == "some-old-model"
