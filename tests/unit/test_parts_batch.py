"""Batch requests for principal parts (greek/api/parts.py): prompts, results, costs."""

import json
from types import SimpleNamespace

import pytest

from greek.api.parts import (
    DEFAULT_OUTPUT_PER_VERB,
    PART_NAMES,
    PARTS_SCHEMA,
    PARTS_SYSTEM_PROMPT,
    PartsOutcome,
    build_parts_prompt,
    build_parts_request,
    check_parts_output,
    estimate_parts,
    group_verbs,
    parts_custom_id,
    read_parts_result,
)
from greek.api.study import BATCH_PRICES, EFFORT, MAX_TOKENS, MODEL
from greek.api.works import ILIAD
from greek.models import Usage

VERBS = ["ἀείδω", "μάχομαι"]


def _parts(*forms, note=""):
    return [{"form": f, "note": note} for f in forms]


def _answer(*verbs):
    parts = {
        "ἀείδω": _parts("ἀείδω", "ἀείσομαι", "ἤεισα", "—", "ᾖσμαι", "ᾔσθην"),
        "μάχομαι": _parts("μάχομαι", "μαχήσομαι", "ἐμαχεσάμην", "—", "μεμάχημαι", "—"),
    }
    return {"verbs": [{"verb": v, "parts": parts[v]} for v in verbs]}


# --- Requests -------------------------------------------------------------------


def test_custom_ids_and_groups():
    assert parts_custom_id(12, 3) == "12_parts_3"
    verbs = [f"v{i}" for i in range(45)]
    groups = group_verbs(verbs)
    assert [len(g) for g in groups] == [20, 20, 5]
    assert [v for g in groups for v in g] == verbs
    assert group_verbs([]) == []


def test_prompt_numbers_the_verbs_and_says_how_many():
    prompt = build_parts_prompt(VERBS)
    assert prompt.splitlines() == [
        'Principal parts of 2 verbs. Give "verbs" 2 objects, in this order.',
        "",
        "1 ἀείδω",
        "2 μάχομαι",
    ]


def test_request_matches_the_study_settings():
    request = build_parts_request(ILIAD, "12_parts_1", VERBS)
    params = request["params"]
    assert request["custom_id"] == "12_parts_1"
    assert params["model"] == MODEL
    assert params["max_tokens"] == MAX_TOKENS
    assert params["output_config"] == {
        "effort": EFFORT,
        "format": {"type": "json_schema", "schema": PARTS_SCHEMA},
    }
    assert params["system"] == [
        {
            "type": "text",
            "text": PARTS_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    ]
    assert params["messages"] == [
        {"role": "user", "content": build_parts_prompt(VERBS)}
    ]
    assert "thinking" not in params and "fallbacks" not in params


def test_prompt_asks_for_homeric_forms_and_six_parts():
    assert "epic or Ionic form" in PARTS_SYSTEM_PROMPT
    assert "exactly six objects" in PARTS_SYSTEM_PROMPT
    assert "suppletive" in PARTS_SYSTEM_PROMPT
    item = PARTS_SCHEMA["properties"]["verbs"]["items"]
    assert item["required"] == ["verb", "parts"]
    assert item["properties"]["parts"]["items"]["required"] == ["form", "note"]


def test_estimate_prices_input_and_output_at_batch_rates():
    requests = [build_parts_request(ILIAD, parts_custom_id(1, 1), VERBS)]
    est = estimate_parts(requests, 2)
    assert est.requests == 1 and est.verbs == 2
    assert est.output_per_verb == DEFAULT_OUTPUT_PER_VERB
    assert est.output_tokens == round(2 * DEFAULT_OUTPUT_PER_VERB)
    prices = BATCH_PRICES[MODEL]
    assert est.cost == pytest.approx(
        (est.input_tokens * prices["input"] + est.output_tokens * prices["output"])
        / 1_000_000
    )
    assert estimate_parts(requests, 2, 100).output_tokens == 200


# --- Checking an answer -----------------------------------------------------------


def test_check_stores_six_named_parts_per_verb():
    parts, failed = check_parts_output(_answer(*VERBS), VERBS)
    assert failed == {}
    assert list(parts) == VERBS
    assert parts["ἀείδω"][0] == {
        "part": 1,
        "name": PART_NAMES[0],
        "form": "ἀείδω",
        "notes": "",
    }
    assert [p["form"] for p in parts["μάχομαι"]][3] == "—"
    assert [p["part"] for p in parts["μάχομαι"]] == [1, 2, 3, 4, 5, 6]


def test_check_finds_verbs_by_name_so_a_skipped_verb_fails_alone():
    data = _answer("μάχομαι")  # ἀείδω skipped, the order shifted
    parts, failed = check_parts_output(data, VERBS)
    assert list(parts) == ["μάχομαι"]
    assert failed == {"ἀείδω": "not in the output"}


def test_check_matches_differently_composed_accents():
    data = _answer("ἀείδω")
    # The same verb with the acute as oxia (U+1F77) instead of tonos
    data["verbs"][0]["verb"] = "ἀείδω"
    parts, failed = check_parts_output(data, ["ἀείδω"])
    assert list(parts) == ["ἀείδω"] and failed == {}


def test_check_finds_a_verb_repeated_with_a_diaeresis():
    # Iliad Book 1: the treebank lemma ἀναίσσω came back as epic ἀναΐσσω
    data = _answer(*VERBS)
    data["verbs"][0]["verb"] = "ἀναΐσσω"
    parts, failed = check_parts_output(data, ["ἀναίσσω", "μάχομαι"])
    assert failed == {}
    assert list(parts) == ["ἀναίσσω", "μάχομαι"]
    assert parts["ἀναίσσω"][1]["form"] == "ἀείσομαι"


def test_check_does_not_guess_between_verbs_that_read_the_same_unaccented():
    data = _answer(*VERBS)
    data["verbs"][0]["verb"] = "ἀείδω"  # asked for both; answered only one
    parts, failed = check_parts_output(data, ["ἀείδω", "ἄειδω", "μάχομαι"])
    assert list(parts) == ["ἀείδω", "μάχομαι"]
    assert failed == {"ἄειδω": "not in the output"}

    data = _answer(*VERBS)
    data["verbs"].append({"verb": "ἀειδῶ", "parts": data["verbs"][0]["parts"]})
    data["verbs"][0]["verb"] = "ἀεῖδω"  # two answers fit ἀείδω unaccented
    parts, failed = check_parts_output(data, VERBS)
    assert list(parts) == ["μάχομαι"]
    assert failed == {"ἀείδω": "not in the output"}


@pytest.mark.parametrize(
    "parts, reason",
    [
        (_parts("ἀείδω", "ἀείσομαι"), "2 parts, not 6"),
        ("ἀείδω", "0 parts, not 6"),
        (_parts("ἀείδω", " ", "ἤεισα", "—", "ᾖσμαι", "ᾔσθην"), "part 2 has no form"),
        ([{"note": ""}] * 6, "part 1 has no form"),
    ],
)
def test_check_rejects_a_verb_without_six_forms(parts, reason):
    data = _answer(*VERBS)
    data["verbs"][0]["parts"] = parts
    stored, failed = check_parts_output(data, VERBS)
    assert list(stored) == ["μάχομαι"]
    assert failed == {"ἀείδω": reason}


def test_check_keeps_notes_and_strips_forms():
    data = _answer("ἀείδω")
    data["verbs"][0]["parts"][1] = {"form": " ἀείσομαι ", "note": " Attic ᾄσομαι "}
    parts, _ = check_parts_output(data, ["ἀείδω"])
    assert parts["ἀείδω"][1]["form"] == "ἀείσομαι"
    assert parts["ἀείδω"][1]["notes"] == "Attic ᾄσομαι"


@pytest.mark.parametrize("data", [[], {}, {"verbs": "ἀείδω"}])
def test_check_needs_a_list_of_verbs(data):
    with pytest.raises(ValueError, match="no list of verbs"):
        check_parts_output(data, VERBS)


def test_summary_names_the_failed_verbs():
    outcome = PartsOutcome("1_parts_1", Usage(), parts={"μάχομαι": []})
    assert outcome.summary is None
    outcome.failed = {"ἀείδω": "not in the output"}
    assert outcome.summary == "1 of 2 verbs not stored: ἀείδω: not in the output"
    assert PartsOutcome("x", Usage(), failure="expired").summary == "expired"


# --- Reading results ----------------------------------------------------------------


def _usage():
    return SimpleNamespace(
        input_tokens=500,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        output_tokens=4000,
        cache_creation=None,
    )


def _message(text, stop_reason="end_turn", **extra):
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        stop_reason=stop_reason,
        usage=_usage(),
        **extra,
    )


def _entry(result, cid="1_parts_1"):
    return SimpleNamespace(custom_id=cid, result=result)


def test_read_result_stores_valid_verbs_with_usage():
    text = json.dumps(_answer("ἀείδω"), ensure_ascii=False)
    message = _message(text)
    outcome = read_parts_result(
        _entry(SimpleNamespace(type="succeeded", message=message)), VERBS
    )
    assert outcome.failure is None
    assert list(outcome.parts) == ["ἀείδω"]
    assert outcome.failed == {"μάχομαι": "not in the output"}
    assert outcome.usage.output_tokens == 4000 and outcome.usage.cost_usd > 0


@pytest.mark.parametrize(
    "result, failure, billed",
    [
        (
            SimpleNamespace(
                type="errored",
                error=SimpleNamespace(
                    type="error",
                    error=SimpleNamespace(type="api_error", message="Internal"),
                ),
            ),
            "error: api_error: Internal",
            False,
        ),
        (SimpleNamespace(type="expired"), "expired", False),
        (
            SimpleNamespace(type="succeeded", message=_message("", "refusal")),
            "refusal",
            True,
        ),
        (
            SimpleNamespace(
                type="succeeded", message=_message('{"verbs": [', "max_tokens")
            ),
            "truncated at max_tokens",
            True,
        ),
        (
            SimpleNamespace(type="succeeded", message=_message("not json")),
            "the output is not valid JSON",
            True,
        ),
        (
            SimpleNamespace(type="succeeded", message=_message('{"answer": 1}')),
            "the output has no list of verbs",
            True,
        ),
    ],
)
def test_read_result_failures(result, failure, billed):
    outcome = read_parts_result(_entry(result), VERBS)
    assert outcome.parts == {}
    assert outcome.failure == failure
    assert (outcome.usage.cost_usd > 0) == billed
