from __future__ import annotations

import io
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin

import requests
from pypdf import PdfReader

from db import get_db_connection
from ingestion.homepagefinder import is_public_http_url
from ingestion.openalex_client import OpenAlexUnavailable, openalex_get_json
from settings import setting, setting_bool, setting_int

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
AFFILIATION_STOPWORDS = {
    "and", "at", "college", "department", "institute", "of", "school",
    "system", "the", "university",
}
EMAIL_PATTERN = re.compile(
    r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE
)


def _ascii_fold(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    )


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _ascii_fold(value).casefold())


def _institution_tokens(value: str) -> set[str]:
    return {
        token
        for token in _tokens(value)
        if len(token) > 2 and token not in AFFILIATION_STOPWORDS
    }


def _institution_matches(institution: str, text: str) -> bool:
    expected = _institution_tokens(institution)
    if not expected:
        return False
    present = set(_tokens(text))
    required = 2 if len(expected) >= 2 else 1
    return len(expected & present) >= required


def _name_matches(name: str, text: str) -> bool:
    expected = _tokens(name)
    if len(expected) < 2:
        return False
    normalized = " ".join(_tokens(text))
    exact = re.escape(" ".join(expected))
    if re.search(rf"\b{exact}\b", normalized):
        return True
    first, last = map(re.escape, (expected[0], expected[-1]))
    return bool(re.search(rf"\b{first}(?:\s+[a-z0-9]+){{1,3}}\s+{last}\b", normalized))


def _openalex_params() -> dict[str, object]:
    params: dict[str, object] = {}
    email = setting("OPENALEX_EMAIL").strip()
    if email:
        params["mailto"] = email
    api_key = setting("OPENALEX_API_KEY").strip()
    if api_key:
        params["api_key"] = api_key
    return params


def _pdf_url_from_work(work: dict[str, Any]) -> str:
    locations = [
        work.get("best_oa_location") or {},
        work.get("primary_location") or {},
        *(work.get("locations") or []),
    ]
    for location in locations:
        value = str((location or {}).get("pdf_url") or "").strip()
        if value and is_public_http_url(value):
            return value
    return ""


def _resolve_open_pdf_url(paper: dict[str, Any]) -> str:
    existing = str(paper.get("pdf_url") or "").strip()
    if existing and is_public_http_url(existing):
        return existing
    work_id = str(paper.get("openalex_id") or "").rstrip("/").rsplit("/", 1)[-1]
    if not work_id:
        return ""
    try:
        work = openalex_get_json(
            f"{OPENALEX_WORKS_URL}/{work_id}",
            params=_openalex_params(),
            timeout=setting_int("PAPER_METADATA_TIMEOUT_SECONDS", 12, 5, 30),
        )
    except (OpenAlexUnavailable, OSError, RuntimeError, ValueError):
        return ""
    return _pdf_url_from_work(work)


def _download_pdf(url: str) -> tuple[bytes, str]:
    maximum = setting_int("PAPER_PDF_MAX_BYTES", 8_000_000, 500_000, 20_000_000)
    timeout = setting_int("PAPER_PDF_TIMEOUT_SECONDS", 15, 5, 45)
    current = url
    headers = {"User-Agent": "ScholarRadar/1.0 (open-access affiliation verifier)"}
    for _redirect in range(4):
        if not is_public_http_url(current):
            raise ValueError("PDF URL is not a public HTTP resource.")
        response = requests.get(
            current,
            headers=headers,
            timeout=timeout,
            stream=True,
            allow_redirects=False,
        )
        try:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = str(response.headers.get("Location") or "").strip()
                if not location:
                    raise ValueError("PDF redirect did not include a destination.")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            length = int(response.headers.get("Content-Length") or 0)
            if length and length > maximum:
                raise ValueError("PDF exceeds the configured download limit.")
            content = bytearray()
            for chunk in response.iter_content(chunk_size=65_536):
                if not chunk:
                    continue
                content.extend(chunk)
                if len(content) > maximum:
                    raise ValueError("PDF exceeds the configured download limit.")
            payload = bytes(content)
            if not payload.lstrip().startswith(b"%PDF"):
                raise ValueError("The open-access URL did not return a PDF.")
            return payload, str(response.url or current)
        finally:
            response.close()
    raise ValueError("PDF redirected too many times.")


def _extract_first_pages(payload: bytes) -> str:
    reader = PdfReader(io.BytesIO(payload), strict=False)
    maximum_pages = setting_int("PAPER_PDF_MAX_PAGES", 3, 1, 5)
    text: list[str] = []
    for page in reader.pages[:maximum_pages]:
        value = page.extract_text() or ""
        if value:
            text.append(value)
    return "\n".join(text)


def _ocr_first_pages(payload: bytes) -> str:
    """OCR a bounded number of PDF pages when they contain no usable text."""
    if not setting_bool("PAPER_PDF_OCR_ENABLED", True):
        return ""
    try:
        import pymupdf
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""

    command = setting("TESSERACT_CMD").strip()
    if command:
        pytesseract.pytesseract.tesseract_cmd = command
    maximum_pages = setting_int("PAPER_PDF_OCR_MAX_PAGES", 2, 1, 3)
    dpi = setting_int("PAPER_PDF_OCR_DPI", 180, 120, 240)
    timeout = setting_int("PAPER_PDF_OCR_TIMEOUT_SECONDS", 20, 5, 45)
    text: list[str] = []
    document = None
    try:
        document = pymupdf.open(stream=payload, filetype="pdf")
        matrix = pymupdf.Matrix(dpi / 72, dpi / 72)
        for page_number in range(min(maximum_pages, len(document))):
            pixmap = document[page_number].get_pixmap(matrix=matrix, alpha=False)
            image = Image.frombytes(
                "RGB", (pixmap.width, pixmap.height), pixmap.samples
            )
            value = pytesseract.image_to_string(
                image, config="--psm 6", timeout=timeout
            )
            if value:
                text.append(value)
    except (OSError, RuntimeError, ValueError):
        return ""
    finally:
        if document is not None:
            document.close()
    return "\n".join(text)


def _evidence_excerpt(name: str, institution: str, text: str) -> str:
    compact = " ".join(text.split())
    lowered = _ascii_fold(compact).casefold()
    positions = [
        position
        for position in (
            lowered.find(_ascii_fold(name).casefold()),
            lowered.find(_ascii_fold(institution).casefold()),
        )
        if position >= 0
    ]
    start = max(0, min(positions) - 180) if positions else 0
    return compact[start:start + 1200]


def extract_paper_affiliation(
    candidate: dict[str, Any], paper: dict[str, Any]
) -> dict[str, Any]:
    """Link one authorship to an institution using metadata, then an open PDF."""
    name = str(candidate.get("name") or "").strip()
    institution = str(candidate.get("institution_name") or "").strip()
    raw = str(paper.get("raw_affiliation_text") or "").strip()
    if raw and _institution_matches(institution, raw):
        emails = EMAIL_PATTERN.findall(raw)
        return {
            "status": "MATCHED",
            "source_url": str(paper.get("openalex_id") or paper.get("doi") or ""),
            "affiliation_text": raw[:2000],
            "institution_name": institution,
            "email": emails[0] if emails else "",
            "method": "openalex_raw_affiliation",
        }

    pdf_url = _resolve_open_pdf_url(paper)
    if not pdf_url:
        return {"status": "UNAVAILABLE", "source_url": "", "method": "open_pdf"}
    try:
        payload, final_url = _download_pdf(pdf_url)
        text = _extract_first_pages(payload)
    except (OSError, ValueError, requests.RequestException):
        return {"status": "UNAVAILABLE", "source_url": pdf_url, "method": "open_pdf"}

    method = "open_pdf"
    minimum_text = setting_int("PAPER_PDF_OCR_MIN_TEXT_CHARS", 120, 40, 500)
    if len("".join(text.split())) < minimum_text:
        ocr_text = _ocr_first_pages(payload)
        if ocr_text:
            text = ocr_text
            method = "open_pdf_ocr"
    if not text:
        return {
            "status": "UNAVAILABLE",
            "source_url": final_url,
            "affiliation_text": "",
            "method": "open_pdf_no_text",
        }
    if not _name_matches(name, text) or not _institution_matches(institution, text):
        return {
            "status": "NOT_FOUND",
            "source_url": final_url,
            "affiliation_text": _evidence_excerpt(name, institution, text),
            "method": method,
        }
    emails = EMAIL_PATTERN.findall(text)
    return {
        "status": "MATCHED",
        "source_url": final_url,
        "affiliation_text": _evidence_excerpt(name, institution, text),
        "institution_name": institution,
        "email": emails[0] if emails else "",
        "method": method,
    }


def _result_is_fresh(paper: dict[str, Any]) -> bool:
    checked_at = paper.get("affiliation_checked_at")
    if not isinstance(checked_at, datetime):
        return False
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    days = setting_int("PAPER_AFFILIATION_RETRY_DAYS", 1, 1, 30)
    return checked_at >= datetime.now(timezone.utc) - timedelta(days=days)


def _cached_result(paper: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": str(paper.get("affiliation_status") or "NOT_CHECKED"),
        "source_url": str(paper.get("affiliation_source_url") or ""),
        "affiliation_text": str(paper.get("affiliation_text") or ""),
        "institution_name": str(paper.get("affiliation_institution") or ""),
        "email": str(paper.get("affiliation_email") or ""),
        "method": "cached_paper_affiliation",
    }


def enrich_candidate_metadata_affiliations(
    candidate: dict[str, Any], max_papers: int = 3
) -> dict[str, Any]:
    """Read stored OpenAlex authorship metadata without PDFs or API requests."""
    enriched = dict(candidate)
    papers = [dict(paper) for paper in list(candidate.get("recent_papers") or [])]
    evidence: list[dict[str, Any]] = []
    for paper in papers[: max(1, min(int(max_papers), 3))]:
        if str(paper.get("affiliation_status") or "") == "MATCHED":
            result = _cached_result(paper)
        else:
            raw = str(paper.get("raw_affiliation_text") or "").strip()
            institution = str(candidate.get("institution_name") or "").strip()
            if not raw or not _institution_matches(institution, raw):
                continue
            emails = EMAIL_PATTERN.findall(raw)
            result = {
                "status": "MATCHED",
                "source_url": str(paper.get("openalex_id") or paper.get("doi") or ""),
                "affiliation_text": raw[:2000],
                "institution_name": institution,
                "email": emails[0] if emails else "",
                "method": "openalex_raw_affiliation",
            }
        paper["paper_affiliation"] = result
        evidence.append(result)
    enriched["recent_papers"] = papers
    enriched["paper_affiliations"] = evidence
    return enriched


def _save_result(
    professor_id: int, paper: dict[str, Any], result: dict[str, Any]
) -> None:
    paper_id = paper.get("paper_id")
    if not paper_id:
        return
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE papers
                SET pdf_url = COALESCE(NULLIF(%s, ''), pdf_url),
                    pdf_checked_at = NOW()
                WHERE id = %s
                """,
                (result.get("source_url"), paper_id),
            )
            cursor.execute(
                """
                UPDATE professor_papers
                SET affiliation_status = %s,
                    affiliation_text = %s,
                    affiliation_source_url = %s,
                    affiliation_institution = %s,
                    affiliation_email = %s,
                    affiliation_checked_at = NOW()
                WHERE professor_id = %s AND paper_id = %s
                """,
                (
                    result.get("status") or "UNAVAILABLE",
                    result.get("affiliation_text"),
                    result.get("source_url"),
                    result.get("institution_name"),
                    result.get("email"),
                    professor_id,
                    paper_id,
                ),
            )


def enrich_candidate_paper_affiliations(
    candidate: dict[str, Any], max_papers: int | None = None
) -> dict[str, Any]:
    """Inspect at most three recent papers, stopping after a useful match."""
    maximum = max_papers or setting_int("PAPER_AFFILIATION_MAX_PAPERS", 3, 1, 3)
    enriched = dict(candidate)
    papers = [dict(paper) for paper in list(candidate.get("recent_papers") or [])]
    evidence: list[dict[str, Any]] = []
    for paper in papers[:maximum]:
        status = str(paper.get("affiliation_status") or "NOT_CHECKED")
        if status == "MATCHED" or (status != "NOT_CHECKED" and _result_is_fresh(paper)):
            result = _cached_result(paper)
        else:
            result = extract_paper_affiliation(enriched, paper)
            _save_result(int(candidate.get("id") or 0), paper, result)
        paper["paper_affiliation"] = result
        evidence.append(result)
        if result.get("status") == "MATCHED":
            break
    enriched["recent_papers"] = papers
    enriched["paper_affiliations"] = evidence
    return enriched
