"""Documentation copy guards.

F1: the voseo check must use WORD-BOUNDARY matching. A naive substring search
counts legitimate words that merely contain a voseo term (``modales`` contains
``dale``) as false positives. With ``\\b`` boundaries the real check is clean:
zero voseo rioplatense, and the legit ``modales`` is never flagged.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DOCS = ("README.md", "DOCUMENTACION.md")

# Only the unmistakably-voseo accented imperatives/forms (plus "dale", which the
# legacy naive grep flagged via "modales"). The accents avoid colliding with the
# legitimate third-person forms (``usa``, ``hace``, ``espera``, ...).
_VOSEO = re.compile(
    r"\b(?:"
    r"tenés|querés|podés|sabés|vení|andá|fijate|mirá|decime|usá|intentá|"
    r"escribí|hacé|poné|dejá|esperá|decile|tomá|elegí|abrí|cerrá|dale"
    r")\b",
    re.IGNORECASE,
)


def test_docs_have_zero_real_voseo() -> None:
    offenders: list[str] = []
    for name in _DOCS:
        text = (_REPO_ROOT / name).read_text(encoding="utf-8")
        for match in _VOSEO.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{name}:{line_no}: {match.group(0)!r}")
    assert offenders == [], "Voseo rioplatense en la documentación: " + ", ".join(offenders)


def test_word_boundary_ignores_legit_word_containing_dale() -> None:
    # ``modales`` is a legitimate word, NOT voseo, even though it contains "dale".
    assert _VOSEO.search("muestra ft.AlertDialog modales, uno a la vez") is None
    # The naive substring (the old false positive) DOES match it; the boundary
    # regex is what makes the check trustworthy.
    assert re.search("dale", "modales") is not None
    assert re.search(r"\bdale\b", "modales") is None
