"""Deterministic quality gate for Google Scholar publication rows.

The gate deliberately handles only high-confidence cases. Ambiguous rows are
left for the bounded Qwen reviewer instead of being guessed from title text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


ACCEPT = "ACCEPT"
REJECT = "REJECT"
AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class PublicationQualityDecision:
    decision: str
    reasons: tuple[str, ...] = ()


_EXPLICIT_SERVICE_TITLE = re.compile(
    r"(?:"
    r"^program\s+committee(?:\s+.+)?$|"
    r"^artifact\s+program\s+committee$|"
    r"^research\s+track\s+program\s+committee$|"
    r"^workshop\s+organization$|"
    r"^organizing\s+committee$|"
    r"^steering\s+committee$|"
    r"^doctoral\s+symposium$|"
    r"^message\s+from\s+.+\s+chairs?$|"
    r"\b(?:committee|committees)\s*$|"
    r"\b(?:organization|organizing committee)\s*$|"
    r"\(\s*chair\s*\)\s*$"
    r")",
    re.I,
)

# Short event labels such as "SBST 2017", "SEAMS 2021", "ACSOS 2024" are
# service/event records, not paper titles. Longer workshop/proceedings titles
# remain ambiguous and can be reviewed by Qwen.
_SHORT_EVENT_ONLY = re.compile(
    r"^(?:[A-Z][A-Za-z0-9@+.-]*)(?:\s+[A-Z][A-Za-z0-9@+.-]*){0,2}\s+(?:19|20)\d{2}$"
)

_EVENT_HEADING = re.compile(
    r"(?:"
    r"^(?:the\s+)?\d+(?:st|nd|rd|th)\s+(?:international\s+)?(?:workshop|symposium|conference)\b|"
    r"^(?:19|20)\d{2}\s+(?:ieee/?acm\s+)?(?:international\s+)?(?:workshop|symposium|conference)\b|"
    r"\b(?:workshop|symposium|conference)\s*@?\s*(?:icse|seams|acsos|sbst|sbft)\b|"
    r"@\s*(?:icse|seams|acsos|sbst|sbft)\s+(?:19|20)\d{2}$|"
    r"\([A-Za-z][A-Za-z0-9@+.-]{1,15}\s+(?:19|20)\d{2}\)\s*$"
    r")",
    re.I,
)

_PERSON_LIST_LIKE = re.compile(
    r"\b(?:university|institute|grammatech|meta|optimatics|carnegie mellon|college london)\b",
    re.I,
)

_DOI = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def scholar_publication_quality(
    *, title: str, authors: str = "", venue: str = "", year: int | None = None,
    evidence: str = "",
) -> PublicationQualityDecision:
    """Return ACCEPT, REJECT, or AMBIGUOUS for one Scholar row.

    Only obvious service/event junk is automatically rejected. Only rows with
    strong publication-shaped metadata are automatically accepted. Everything
    else is deliberately routed to the model/manual-review path.
    """
    title = _clean(title)
    authors = _clean(authors)
    venue = _clean(venue)
    evidence = _clean(evidence)
    if not title:
        return PublicationQualityDecision(REJECT, ("empty_title",))

    folded = title.casefold()
    words = re.findall(r"[a-z0-9]+", folded)

    if _EXPLICIT_SERVICE_TITLE.search(title):
        return PublicationQualityDecision(REJECT, ("explicit_service_or_committee_title",))

    if len(words) <= 4 and _SHORT_EVENT_ONLY.fullmatch(title):
        return PublicationQualityDecision(REJECT, ("short_event_name_with_year",))

    # Rows that are mostly a pasted list of people/affiliations are not safe to
    # index as papers automatically, but they are sent to Qwen rather than
    # deleted deterministically.
    if title.count(",") >= 3 and _PERSON_LIST_LIKE.search(title):
        return PublicationQualityDecision(AMBIGUOUS, ("person_or_affiliation_list_like_title",))

    # Workshop/proceedings headings can represent an editorial/proceedings
    # publication, or just service. Keep them out of deterministic decisions.
    if _EVENT_HEADING.search(title):
        return PublicationQualityDecision(AMBIGUOUS, ("event_or_proceedings_heading",))
    if len(words) <= 10 and re.search(r"\([A-Za-z][A-Za-z0-9@+.-]{2,15}\)\s*$", title):
        return PublicationQualityDecision(AMBIGUOUS, ("short_title_ending_in_event_acronym",))

    if _DOI.search(" ".join((title, venue, evidence))):
        return PublicationQualityDecision(ACCEPT, ("doi_present",))

    has_authors = len(re.findall(r"[A-Za-z][A-Za-z'.-]+", authors)) >= 2
    has_venue = len(venue) >= 4
    has_year = year is not None

    if has_authors and has_venue and len(words) >= 4:
        return PublicationQualityDecision(ACCEPT, ("authors_and_venue_present",))
    if has_authors and has_year and len(words) >= 6:
        return PublicationQualityDecision(ACCEPT, ("authors_year_and_substantive_title",))
    if has_venue and has_year and len(words) >= 7:
        return PublicationQualityDecision(ACCEPT, ("venue_year_and_substantive_title",))

    return PublicationQualityDecision(AMBIGUOUS, ("insufficient_metadata_for_deterministic_decision",))
