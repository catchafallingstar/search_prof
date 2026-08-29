from __future__ import annotations

import json
from datetime import date
from typing import Any
from urllib.parse import quote

import requests

from db import get_db_connection
from settings import setting, setting_bool, setting_int


IDENTITY_AI_PROMPT_VERSION = 1
IDENTITY_AI_PROVIDER = "gemini"


def identity_ai_enabled() -> bool:
    """Return whether the optional, budgeted identity assistant is configured."""
    return bool(
        setting_bool("GEMINI_IDENTITY_ENABLED", False)
        and setting("GEMINI_API_KEY").strip()
    )


def _reserve_daily_request(limit: int) -> bool:
    """Atomically reserve one shared request across workers and app restarts."""
    if limit <= 0:
        return False
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO ai_usage_daily (
                        usage_date, provider, feature, request_count, updated_at
                    ) VALUES (CURRENT_DATE, %s, 'faculty_identity', 1, NOW())
                    ON CONFLICT (usage_date, provider, feature) DO UPDATE
                    SET request_count = ai_usage_daily.request_count + 1,
                        updated_at = NOW()
                    WHERE ai_usage_daily.request_count < %s
                    RETURNING request_count
                    """,
                    (IDENTITY_AI_PROVIDER, limit),
                )
                return cursor.fetchone() is not None
    except Exception:
        # Missing schema or database trouble must never stop the rule-based
        # verifier, and must never cause an unmetered API call.
        return False


def _page_payload(
    pages: list[dict[str, Any]],
    max_input_chars: int,
) -> list[dict[str, str]]:
    remaining = max(2_000, max_input_chars)
    compact: list[dict[str, str]] = []
    for page in pages[:3]:
        url = str(page.get("source_url") or "").strip()
        text = " ".join(str(page.get("_page_text") or "").split())
        if not url or not text or remaining <= 0:
            continue
        text = text[:remaining]
        remaining -= len(text)
        compact.append(
            {
                "url": url,
                "page_title": str(page.get("page_title") or "")[:500],
                "rule_status": str(page.get("status") or "UNVERIFIED"),
                "inferred_institution": str(page.get("institution_name") or "")[:300],
                "content": text,
            }
        )
    return compact


def _response_schema() -> dict[str, Any]:
    return {
        "type": "OBJECT",
        "properties": {
            "decision": {
                "type": "STRING",
                "enum": ["VERIFIED", "NOT_FACULTY", "CONFLICT", "UNVERIFIED"],
            },
            "selected_source_url": {"type": "STRING"},
            "observed_title": {"type": "STRING"},
            "observed_institution": {"type": "STRING"},
            "identity_evidence_quote": {"type": "STRING"},
            "identity_link_quote": {"type": "STRING"},
            "reason": {"type": "STRING"},
            "confidence": {"type": "NUMBER"},
        },
        "required": [
            "decision",
            "selected_source_url",
            "observed_title",
            "observed_institution",
            "identity_evidence_quote",
            "identity_link_quote",
            "reason",
            "confidence",
        ],
    }


def assess_identity_with_gemini(
    candidate: dict[str, Any],
    pages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Ask Gemini to extract evidence only after deterministic rules are unsure.

    The caller must independently validate all returned URLs, quotations, titles,
    and identity links. A schema-valid model response is not a faculty decision.
    """
    if not identity_ai_enabled():
        return None
    daily_limit = setting_int("GEMINI_IDENTITY_DAILY_LIMIT", 25, 0, 500)
    compact_pages = _page_payload(
        pages,
        setting_int("GEMINI_IDENTITY_MAX_INPUT_CHARS", 16_000, 2_000, 50_000),
    )
    if not compact_pages or not _reserve_daily_request(daily_limit):
        return None

    model = setting("GEMINI_IDENTITY_MODEL", "gemini-2.5-flash-lite").strip()
    papers = [
        {
            "title": str(paper.get("title") or "")[:500],
            "year": paper.get("publication_year"),
            "doi": str(paper.get("doi") or "")[:200],
            "author_position": str(paper.get("author_position") or "")[:50],
        }
        for paper in list(candidate.get("recent_papers") or [])[:5]
    ]
    case = {
        "candidate": {
            "name": str(candidate.get("name") or ""),
            "openalex_id": str(candidate.get("openalex_id") or ""),
            "paper_affiliation": str(candidate.get("institution_name") or ""),
            "research_area": str(candidate.get("research_domain") or ""),
            "recent_papers": papers,
        },
        "official_pages": compact_pages,
    }
    prompt = (
        "You are an evidence extractor for a faculty identity database. "
        "Use only the supplied official-page content. Do not use outside knowledge. "
        "A last-author paper, Google Scholar profile, or similar research topic does not "
        "prove faculty status. VERIFIED requires an eligible professor title and evidence "
        "that this official page belongs to the same researcher as the candidate. "
        "NOT_FACULTY requires an explicit current student, postdoc, or staff role. "
        "CONFLICT means a plausible official page exists but the person cannot be safely "
        "linked to the candidate. Copy both evidence fields verbatim from one supplied "
        "page. If evidence is insufficient, return UNVERIFIED.\n\nCASE:\n"
        + json.dumps(case, ensure_ascii=False)
    )
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{quote(model, safe='-._')}:generateContent"
    )
    try:
        response = requests.post(
            endpoint,
            headers={
                "x-goog-api-key": setting("GEMINI_API_KEY").strip(),
                "Content-Type": "application/json",
            },
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0,
                    "maxOutputTokens": 700,
                    "responseMimeType": "application/json",
                    "responseSchema": _response_schema(),
                },
            },
            timeout=setting_int("GEMINI_IDENTITY_TIMEOUT_SECONDS", 20, 5, 60),
        )
        response.raise_for_status()
        payload = response.json()
        raw_text = payload["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(raw_text)
        if not isinstance(result, dict):
            return None
        result["model_name"] = model
        result["prompt_version"] = IDENTITY_AI_PROMPT_VERSION
        result["usage_date"] = date.today().isoformat()
        return result
    except (KeyError, IndexError, TypeError, ValueError, requests.RequestException):
        return None
