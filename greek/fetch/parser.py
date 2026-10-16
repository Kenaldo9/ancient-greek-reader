"""
General TEI XML parser for Perseus texts.
Auto-detects poetry (<l> elements) vs prose (nested <div> sections).
"""

import xml.etree.ElementTree as ET
from typing import List, Tuple

NS = "http://www.tei-c.org/ns/1.0"


def _tag(elem) -> str:
    return elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag


# Editorial apparatus inside the Greek: footnote markers (Herodotus' "1") and
# citations of quoted passages ("Hom. Il. 6.289-292"). Each is read as a space:
# the TEI often has no other between the words around it (Δαρείου<note>1
# </note>οὐδέν). In a <choice>, the misprint its <corr> corrects is left out.
_APPARATUS = {"note", "bibl"}


def _texts(elem):
    if elem.text:
        yield elem.text
    for child in elem:
        tag = _tag(child)
        if tag in _APPARATUS:
            yield " "
        elif not (tag == "sic" and _tag(elem) == "choice"):
            yield from _texts(child)
        if child.tail:
            yield child.tail


def _get_text(elem) -> str:
    return " ".join("".join(_texts(elem)).split())


def extract_entries(xml_bytes: bytes) -> List[Tuple[str, str]]:
    """
    Parse TEI XML and return (reference, content) pairs.
    Poetry: reference is book.line  e.g. "1.1"
    Prose:  reference is the chain of ancestor div n-values e.g. "1.1.1"
    """
    root = ET.fromstring(xml_bytes)
    results: List[Tuple[str, str]] = []

    # Prose texts (Thucydides, Herodotus, Plato...) often contain quoted verse
    # inside <l> tags without @n. Only treat as poetry if numbered <l n="..."> exist.
    is_poetry = root.find(f".//{{{NS}}}l[@n]") is not None

    if is_poetry:
        _walk_poetry(root, [], results)
    else:
        _walk_prose(root, [], results)

    return results


def _walk_poetry(elem, path: List[str], out: List[Tuple[str, str]]) -> None:
    tag = _tag(elem)
    n = elem.get("n", "")

    if tag == "l" and n:
        text = _get_text(elem).strip()
        if text:
            out.append((".".join(path + [n]), text))
        return  # don't recurse into line elements

    new_path = path + [n] if (tag == "div" and n and not n.startswith("urn")) else path
    for child in elem:
        _walk_poetry(child, new_path, out)


def _walk_prose(elem, path: List[str], out: List[Tuple[str, str]]) -> None:
    tag = _tag(elem)
    n = elem.get("n", "")

    if tag == "div" and n and not n.startswith("urn"):
        new_path = path + [n]
        has_sub_divs = any(_tag(c) == "div" for c in elem)
        if not has_sub_divs and new_path:
            text = _get_text(elem).strip()
            if text:
                out.append((".".join(new_path), text))
            return
        for child in elem:
            _walk_prose(child, new_path, out)
    else:
        for child in elem:
            _walk_prose(child, path, out)
