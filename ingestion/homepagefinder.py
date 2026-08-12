import ipaddress
import os
import random
import re
import socket
import threading
import time
from urllib.parse import urlparse

from ddgs import DDGS

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
_search_lock = threading.Lock()


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
    queries = [
        f'"{prof_name}" "{clean_institution}" faculty homepage',
        f'"{prof_name}" "{clean_institution}" lab website',
    ]
    proxy_url = os.getenv("ROTATING_PROXY_URL")
    for query in queries:
        try:
            with _search_lock:
                time.sleep(random.uniform(0.4, 0.9))
            kwargs: dict[str, object] = {"timeout": 8}
            if proxy_url:
                kwargs["proxy"] = proxy_url
            results = list(DDGS(**kwargs).text(query, max_results=5))
            for result in results:
                candidate = result.get("href", "")
                if is_valid_homepage(candidate) and _result_matches_professor(prof_name, result):
                    return candidate
        except Exception as error:
            print(f"Homepage search failed for {prof_name}: {error}")
    return ""
