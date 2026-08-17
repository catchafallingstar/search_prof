import ipaddress
import re
import socket
from urllib.parse import urlparse

from ingestion.websearch import search_web

EXCLUDED_DOMAINS = {
    "academia.edu",
    "amazon.com",
    "facebook.com",
    "google.com",
    "linkedin.com",
    "ratemyprofessors.com",
    "researchgate.net",
    "scholar.google.com",
    "ssrn.com",
    "wikipedia.org",
}
EXCLUDED_SUFFIXES = (".pdf", ".doc", ".docx", ".ppt", ".pptx", ".zip", ".epub")
INSTITUTION_STOP_WORDS = {
    "and", "at", "college", "institute", "of", "school", "the", "university"
}


def _result_matches_professor(prof_name: str, result: dict[str, object]) -> bool:
    parts = re.findall(r"[a-z0-9]+", prof_name.casefold())
    if not parts:
        return False
    combined = " ".join(
        str(result.get(key) or "") for key in ("title", "body", "href")
    ).casefold()
    if len(parts) == 1:
        return parts[0] in combined
    return parts[0] in combined and parts[-1] in combined


def result_matches_institution(institution: str, *values: object) -> bool:
    """Require a distinctive institution token or a familiar initialism."""
    words = re.findall(r"[a-z0-9]+", (institution or "").casefold())
    distinctive = [word for word in words if word not in INSTITUTION_STOP_WORDS and len(word) > 2]
    if not distinctive:
        return True
    combined = " ".join(str(value or "") for value in values).casefold()
    combined_tokens = set(re.findall(r"[a-z0-9]+", combined))
    if any(word in combined_tokens for word in distinctive):
        return True
    initialism = "".join(word[0] for word in words if word not in {"and", "at", "of", "the"})
    return len(initialism) >= 2 and initialism in combined_tokens


def is_public_http_url(url: str, resolve_dns: bool = False) -> bool:
    try:
        parsed = urlparse(str(url).strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        hostname = parsed.hostname.casefold().rstrip(".")
        if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
            return False
        if any(hostname == domain or hostname.endswith(f".{domain}") for domain in EXCLUDED_DOMAINS):
            return False
        if parsed.path.casefold().endswith(EXCLUDED_SUFFIXES):
            return False
        try:
            literal_ip = ipaddress.ip_address(hostname)
            if not literal_ip.is_global:
                return False
        except ValueError:
            pass
        if resolve_dns:
            addresses = {item[4][0] for item in socket.getaddrinfo(hostname, None)}
            if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
                return False
        return True
    except (OSError, ValueError):
        return False


def is_valid_homepage(url: str) -> bool:
    return is_public_http_url(url, resolve_dns=False)


def get_professor_homepage(
    prof_name: str,
    institution: str,
    openalex_homepage: str | None = None,
    **_: object,
) -> str:
    if openalex_homepage and is_valid_homepage(openalex_homepage):
        return openalex_homepage.strip()

    clean_institution = (institution or "").split("(")[0].strip()
    queries = [f'"{prof_name}" "{clean_institution}" faculty lab homepage']
    for query in queries:
        try:
            results = search_web(query, max_results=3)
            for result in results:
                candidate = result.get("href", "")
                if (
                    is_valid_homepage(candidate)
                    and _result_matches_professor(prof_name, result)
                    and result_matches_institution(
                        institution,
                        result.get("title"),
                        result.get("body"),
                        result.get("href"),
                    )
                ):
                    return candidate
        except Exception as error:
            print(f"Homepage search failed for {prof_name}: {error}")
    return ""
