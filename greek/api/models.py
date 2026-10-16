"""Display names of the models that made saved data (the study batches)."""

LABELS = {
    "claude-opus-5-5": "Claude Opus 5.5",
    "claude-fable-5-1": "Claude Fable 5.1",
    "claude-sonnet-5-5": "Claude Sonnet 5.5",
    "claude-opus-4-6": "Claude Opus 4.6",
}


def model_label(model_id: str) -> str:
    """Return the display label for a model id; unknown ids are returned as-is."""
    return LABELS.get(model_id, model_id)
