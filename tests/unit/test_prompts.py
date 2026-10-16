from greek.api.prompts import GLOSS_RULE


def test_study_prompt_shares_the_gloss_rule():
    from greek.api.study import STUDY_SYSTEM_PROMPT

    assert f'- "gloss": {GLOSS_RULE}\n' in STUDY_SYSTEM_PROMPT
    assert "one to four English words" in GLOSS_RULE
