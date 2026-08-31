"""Conservative author-to-university linking, not whole-document co-occurrence."""
import re
import unicodedata

_WORD = r"[A-Z][\w’'&-]*"
_UNIVERSITY = re.compile(
    rf"\b(?:University[ \t]+of[ \t]+{_WORD}(?:[ \t]+(?:{_WORD}|at|of|the)){{0,8}}"
    rf"|{_WORD}(?:[ \t]+(?:{_WORD}|of|the|and)){{0,7}}[ \t]+(?:University|College|Institute[ \t]+of[ \t]+Technology))\b"
)


def institutions_in_text(text: str) -> list[str]:
    names = []
    primary = list(re.finditer(rf"\b(?:State[ \t]+)?University[ \t]+of[ \t]+{_WORD}(?:[ \t]+(?:{_WORD}|at|of|the)){{0,8}}", text))
    for match in primary:
        if match.group() not in names:
            names.append(match.group())
    for match in _UNIVERSITY.finditer(text):
        if any(match.start() < p.end() and p.start() < match.end() for p in primary):
            continue
        name = match.group().strip()
        # Do not absorb author/title prefixes into the institution name.
        name = re.split(r"\b(?:Department of [^,]+,|Professor at|at)\s+", name)[-1]
        if name and name not in names:
            names.append(name)
    return names


def author_institution(name: str, text: str) -> tuple[str, str]:
    """Return a uniquely linked institution + excerpt, or empty for ambiguity.

    Handles a single shared affiliation, same-line affiliation, consecutive author
    blocks and simple numbered superscripts. Complex multi-column layouts stay
    unresolved rather than borrowing another author's university.
    """
    header = re.split(r"\b(?:abstract|introduction|references|acknowledg(?:e)?ments)\b", text, maxsplit=1, flags=re.I)[0][:10000]
    header = unicodedata.normalize("NFKC", header)
    tokens = re.findall(r"[\w’'-]+", unicodedata.normalize("NFKC", name))
    if len(tokens) < 2:
        return "", ""
    name_pattern = r"\b" + r"\s+".join(map(re.escape, tokens)) + r"(?=\W|[1-9]|$)"
    match = re.search(name_pattern, header, flags=re.I)
    if not match:
        return "", ""
    universities = institutions_in_text(re.sub(name_pattern, "\n", header, flags=re.I))
    if len(universities) == 1:
        return universities[0], header[max(0, match.start() - 80):match.end() + 1000]
    if not universities:
        return "", ""
    lines = header.splitlines()
    for position, line in enumerate(lines):
        found = re.search(name_pattern, line, flags=re.I)
        if not found:
            continue
        same_line = institutions_in_text(line[found.end():])
        if len(same_line) == 1:
            return same_line[0], line[:1200]
        suffix = line[found.end():]
        marker = re.match(r"\s*([1-9])(?:\s*[,;]\s*[1-9])?(?=\s|[,;*†‡]|$)", suffix)
        if marker and not re.search(r"[,;]\s*[1-9]", marker.group()):
            targets = []
            for affiliation in lines:
                if re.match(rf"\s*{marker.group(1)}(?:\s|[.)])", affiliation):
                    targets.extend((university, affiliation) for university in institutions_in_text(affiliation))
            if len(targets) == 1:
                return targets[0][0], f"{line}\n{targets[0][1]}"[:1200]
        # A dedicated name line immediately followed by its institution is safe;
        # never traverse another author's line looking for a later institution.
        remainder = re.sub(name_pattern, "", line, flags=re.I).strip(" .,;*†‡0123456789")
        if not remainder:
            block = [part.strip() for part in lines[position + 1:position + 4] if part.strip()]
            if block and block[0].lower().startswith("department ") and not institutions_in_text(block[0]):
                block = block[1:]
            if block:
                linked = institutions_in_text(block[0])
                if len(linked) == 1:
                    return linked[0], f"{line}\n{block[0]}"
    return "", header[max(0, match.start() - 80):match.end() + 1000]
