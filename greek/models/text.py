from dataclasses import dataclass
from typing import Optional


@dataclass
class TextMeta:
    id: int
    title: str
    author: Optional[str]
    filename: str
    filepath: str
    line_count: int
    imported_at: str


@dataclass
class Line:
    id: int
    text_id: int
    line_number: int
    content: str
    reference: Optional[str] = None


@dataclass
class Book:
    """A top-level division of a text (the part of a reference before the first dot)."""

    number: str
    ref_start: str
    ref_end: str
    line_start: int
    line_end: int
    count: int
