"""Conservative name folding shared by search retrieval and identity checks.

The original spelling remains the source of truth.  These helpers only create
comparison/search forms, so a transliteration can locate a profile but never
proves that two people are the same without the usual role and institution
checks.
"""
from __future__ import annotations

import re
import unicodedata


# NFKD removes combining accents, but several common Latin letters (notably
# Turkish dotless i) do not decompose.  Cover those explicitly without adding
# a runtime dependency or changing names stored/displayed by ScholarRadar.
_LATIN_FALLBACKS = str.maketrans({
    "ı": "i", "İ": "I",
    "ł": "l", "Ł": "L",
    "đ": "d", "Đ": "D", "ð": "d", "Ð": "D",
    "þ": "th", "Þ": "Th",
    "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
    "ø": "o", "Ø": "O",
    "ħ": "h", "Ħ": "H", "ŧ": "t", "Ŧ": "T",
    "ŋ": "n", "Ŋ": "N", "ƒ": "f",
    "ə": "e", "Ə": "E", "ſ": "s", "ĸ": "k",
    "ß": "ss",
})
_APOSTROPHES = str.maketrans("", "", "'’ʻ`ʼ")
_HONORIFICS = {"dr", "prof", "professor", "mr", "mrs", "ms"}
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd", "md", "pe"}


def fold_name_text(value: str) -> str:
    """Return a stable Latin comparison form while preserving word spacing."""
    compact = str(value or "").translate(_APOSTROPHES).translate(_LATIN_FALLBACKS)
    decomposed = unicodedata.normalize("NFKD", compact)
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def fold_name(value: str) -> str:
    """Compatibility name for the display-case-preserving search fold."""
    return fold_name_text(value)


def search_name_aliases(value: str) -> list[str]:
    """Keep the real spelling first and add one folded retrieval spelling."""
    original = " ".join(str(value or "").split())
    folded = " ".join(fold_name_text(original).split())
    return list(dict.fromkeys(item for item in (original, folded) if item))


def name_tokens(value: str) -> list[str]:
    """Tokenize a person name or surrounding text using the shared fold."""
    return [
        token
        for token in re.findall(r"[^\W_]+", fold_name_text(value).casefold(), re.UNICODE)
        if len(token) > 1
    ]


def canonical_name_key(value: str) -> str:
    """Return an order-aware identity key that preserves initials.

    Display punctuation, spaces, accents and hyphens do not define a person.
    A comma is treated as an explicit ``surname, given`` ordering signal.  We
    intentionally do not reverse an unpunctuated name because guessing whether
    ``Li Wei`` is family-name-first can merge two different people.

    Examples::

        M.Z. Naser   -> mznaser
        M. Z. Naser  -> mznaser
        Naser, M.Z.  -> mznaser
        Smith-Jones  -> smithjones
        Smith Jones  -> smithjones
    """
    folded = fold_name_text(str(value or "")).casefold().strip()
    if not folded:
        return ""
    if "," in folded:
        family, remainder = folded.split(",", 1)
        remainder_tokens = re.findall(r"[^\W_]+", remainder, re.UNICODE)
        if remainder_tokens and all(token in _SUFFIXES for token in remainder_tokens):
            folded = family
        else:
            folded = f"{remainder} {family}"
    tokens = re.findall(r"[^\W_]+", folded, re.UNICODE)
    while tokens and tokens[0] in _HONORIFICS:
        tokens.pop(0)
    while tokens and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return "".join(tokens)
