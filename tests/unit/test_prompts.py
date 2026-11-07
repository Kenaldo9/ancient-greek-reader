from greek.api.prompts import GLOSS_RULE


def test_study_prompt_shares_the_gloss_rule():
    from greek.api.study import STUDY_SYSTEM_PROMPT

    assert f'- "gloss": {GLOSS_RULE}\n' in STUDY_SYSTEM_PROMPT
    assert "one to four English words" in GLOSS_RULE


def test_prose_prompts_keep_articles_glossed_and_notes_free_of_word_numbers():
    # An empty gloss on an article fails the whole unit at collect (Herodotus
    # 1.77), and the reader shows no word numbers for a note to cite.
    from greek.api.study import notes_system_prompt, study_system_prompt
    from greek.api.works import work_profile

    for name in ("herodotus", "thucydides"):
        work = work_profile(name)
        for repeat_words in (False, True):
            prompt = study_system_prompt(work, repeat_words)
            assert f'- "gloss": {GLOSS_RULE} Give an article' in prompt
            assert 'as before a name, as "(the)". A gloss is never empty.' in prompt
            assert "never by its number" in prompt
        assert "never by its number" in notes_system_prompt(work)


def test_verse_prompts_do_not_change_with_the_prose_rules():
    from greek.api.study import notes_system_prompt, study_system_prompt
    from greek.api.works import work_profile

    for name in ("iliad", "odyssey"):
        work = work_profile(name)
        study = study_system_prompt(work)
        assert f'- "gloss": {GLOSS_RULE}\n' in study
        assert "never by its number" not in study
        assert "never by its number" not in notes_system_prompt(work)
