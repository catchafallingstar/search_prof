import hashlib
import re
from datetime import datetime, timezone

ACTION_VERBS = r"(?:look(?:ing)?\s+for|plan[s]?\s+to\s+recruit|recruit(?:ing)?|seek(?:ing)?|accept(?:ing)?|welcom(?:e|ing)|invit(?:e|ing)|sponsor(?:ing)?|looking\s+to\s+hire|join\s+(?:my|our)\s+(?:lab|group|team))"
TARGET_ROLES = r"(?:phd[s]?|doctoral|graduate\s+student[s]?|ph\.d\.|postdoc[s]?|post-doc[s]?|research\s+assistant[s]?|intern[s]?)"

FLEXIBLE_HIRING_PATTERN = re.compile(
    rf"\b{ACTION_VERBS}\s+(?:\w+\W+){{0,8}}?{TARGET_ROLES}\b",
    re.IGNORECASE,
)
FUNDING_PATTERNS = re.compile(
    r"(?:sponsored\s+by|funded\s+by|grants?\s+from|schmidt\s+sciences|nsf\s+career|open\s+philanthropy|darpa|nih|dod|onr)",
    re.IGNORECASE,
)
NEGATIVE_PATTERNS = re.compile(
    r"(?:do\s+not\s+have|not\s+(?:currently\s+)?(?:accepting|recruiting|taking|looking\s+for|seeking)|"
    r"no\s+(?:new\s+)?openings|(?:lab|group)\s+is\s+(?:quite\s+)?full|"
    r"position\s+has\s+been\s+filled)",
    re.IGNORECASE,
)
NON_RECRUITING_CONTEXT_PATTERNS = re.compile(
    r"(?:\bwelcom(?:e|ing)\b.{0,100}\b(?:as|who\s+will\s+be|rotation|joined?)\b|"
    r"\brecruit\s+and\s+retain\b|\bhelp\s+recruit\b|"
    r"\bsecure\b.{0,100}\bfaculty\b.{0,120}\brecruit\b|"
    r"\blook(?:ing)?\s+for\s+opportunities\s+to\s+engage\b|"
    r"\bonce\s+funding\b.{0,100}\b(?:recruit|accept)\b|"
    r"\b(?:contact|email)\s+(?:me|us)\b.{0,100}\b(?:if|whether)\b.{0,100}\baccepting\b|"
    r"\bsee\s+if\s+(?:i|we)\s+(?:am|are)\s+accepting\b)",
    re.IGNORECASE,
)
SEARCH_DATE_PREFIX = re.compile(
    r"^(?P<date>(?:[A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4}|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{1,2}-\d{1,2}))\s*(?:\.{2,3}|[·•|:—-])\s*",
    re.IGNORECASE,
)


def get_text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def is_valid_signal_text(text: str) -> bool:
    return bool(
        text
        and FLEXIBLE_HIRING_PATTERN.search(text)
        and not NEGATIVE_PATTERNS.search(text)
        and not NON_RECRUITING_CONTEXT_PATTERNS.search(text)
    )


def extract_roles_and_funding(text: str) -> tuple[list[str], bool]:
    lowered = text.casefold()
    roles: list[str] = []
    if "postdoc" in lowered or "post-doc" in lowered:
        roles.append("Postdoc")
    if any(token in lowered for token in ("phd", "doctoral", "ph.d.")):
        roles.append("PhD")
    if "intern" in lowered:
        roles.append("Intern")
    if "research assistant" in lowered:
        roles.append("Research Assistant")
    return roles, bool(FUNDING_PATTERNS.search(text))


def _prefix_is_stale(match: re.Match[str], max_age_years: int = 1) -> bool:
    value = match.group("date")
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            observed = datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
            return observed.year < datetime.now(timezone.utc).year - max_age_years
        except ValueError:
            continue
    return False


def clean_and_extract_hiring_quote(raw_snippet: str) -> str:
    if not raw_snippet:
        return ""

    cleaned = raw_snippet.strip()
    # Social widgets sometimes append large counters and unrelated posts to a
    # genuine sentence. They are not evidence and can also look like years.
    cleaned = re.split(
        r"\b(?:Reply|Retweet|Like)\s+on\s+(?:Twitter|X)\b",
        cleaned,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
    prefix = SEARCH_DATE_PREFIX.match(cleaned)
    if prefix:
        if _prefix_is_stale(prefix):
            return ""
        cleaned = SEARCH_DATE_PREFIX.sub("", cleaned)

    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    matches = [sentence.strip() for sentence in sentences if is_valid_signal_text(sentence.strip())]
    if matches:
        return " ".join(matches[:2])[:500]
    return ""
