from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class VerbParts:
    """
    A verb's six principal parts by one model: from a parts batch, in its
    work's dialect ("homeric", "ionic", "attic"). The old Verbs page's
    lookups had none (""); migration 020 removed them.
    """

    id: int
    verb: str
    model_used: str
    looked_up_at: str
    parts: List[Dict[str, Any]] = field(default_factory=list)
    dialect: str = ""
