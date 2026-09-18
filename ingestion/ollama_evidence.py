from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

import requests

from db import get_db_connection
from settings import setting, setting_bool, setting_int


PROMPT_VERSION = "faculty-evidence-v1"
PUBLICATION_PROMPT_VERSION = "publication-identity-v2"
RESEARCH_INTEREST_PROMPT_VERSION = "research-interest-v2"
ALLOWED_RECORD_TYPES = {
    "FACULTY", "EMERITUS", "ADJUNCT", "VISITING", "RESEARCH_FACULTY",
    "LECTURER", "INSTRUCTOR", "STUDENT", "POSTDOC", "STAFF",
    "ADMINISTRATOR", "OTHER", "UNCLEAR",
}
NONFACULTY_TYPES = {"STUDENT", "POSTDOC", "STAFF", "ADMINISTRATOR", "OTHER"}
_unavailable_until = 0.0


@dataclass(frozen=True)
class OllamaReview:
    status: str
    data: dict[str, Any]
    errors: tuple[str, ...] = ()
    cached: bool = False


def enabled() -> bool:
    # Local development uses the on-machine reviewer by default. Production
    # remains opt-in because localhost normally has no Ollama service there.
    development_default = setting("APP_ENV", "").strip().casefold() == "development"
    return setting_bool("OLLAMA_ENABLED", development_default)


def _clean_json(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[-1].rsplit("```", 1)[0]
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("Ollama response must be one JSON object")
    return parsed


def validate_review(data: dict[str, Any], source_text: str) -> tuple[str, ...]:
    errors: list[str] = []
    if str(data.get("record_type") or "").upper() not in ALLOWED_RECORD_TYPES:
        errors.append("invalid record_type")
    evidence = data.get("evidence") or {}
    if not isinstance(evidence, dict):
        errors.append("evidence must be an object")
        evidence = {}
    folded_source = " ".join(source_text.split()).casefold()
    for label, quote in evidence.items():
        if quote in (None, ""):
            continue
        if not isinstance(quote, str):
            errors.append(f"evidence.{label} must be text")
        elif " ".join(quote.split()).casefold() not in folded_source:
            errors.append(f"evidence.{label} is not in source")
    if not isinstance(data.get("conflicts", []), list):
        errors.append("conflicts must be a list")
    return tuple(errors)


def _prompt(kind: str, institution: str, directory_url: str, source_text: str) -> str:
    allowed = ", ".join(sorted(ALLOWED_RECORD_TYPES))
    return f"""Extract evidence from ONE proposed university person record.
Use only the supplied record. Never guess or combine neighboring people.
If name, email, URL, and role are not one coherent identity, set
card_boundary_valid=false, identity_coherent=false, and list each conflict.
Copy exact evidence quotations. Represent missing facts with empty strings.
Return JSON only with these keys: record_type, subject_name, preferred_name,
name_aliases, role_exact, department, email, profile_url,
card_boundary_valid, identity_coherent, same_person, conflicts, evidence,
confidence. record_type must be one of: {allowed}.

SOURCE_KIND: {kind}
EXPECTED_INSTITUTION: {institution}
DIRECTORY_URL: {directory_url}
SOURCE_RECORD:
{source_text[:24000]}
"""


def review(
    *, source_type: str, source_record_key: str, institution_id: int,
    institution: str, directory_url: str, source_text: str,
) -> OllamaReview:
    """Review evidence with Ollama, caching by source hash and prompt version."""
    global _unavailable_until
    if not enabled() or not source_text.strip():
        return OllamaReview("DISABLED", {})
    if time.monotonic() < _unavailable_until:
        return OllamaReview("MODEL_COOLDOWN", {}, ("circuit breaker is open",))
    model = setting("OLLAMA_MODEL", "qwen2.5-coder:7b").strip()
    input_hash = hashlib.sha256(source_text.encode("utf-8", "replace")).hexdigest()
    cooldown_seconds = setting_int("OLLAMA_COOLDOWN_SECONDS", 900, 30, 86400)
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT parsed_response, validation_status, validation_errors
                   FROM ollama_extraction_runs
                   WHERE source_type=%s AND source_record_key=%s AND model=%s
                     AND prompt_version=%s AND input_hash=%s""",
                (source_type, source_record_key, model, PROMPT_VERSION, input_hash),
            )
            cached = cursor.fetchone()
            if not cached:
                cursor.execute(
                    """SELECT 1 FROM ollama_extraction_runs
                       WHERE model=%s AND validation_status='MODEL_UNAVAILABLE'
                         AND created_at > NOW() - (%s * INTERVAL '1 second')
                       LIMIT 1""",
                    (model, cooldown_seconds),
                )
                if cursor.fetchone():
                    return OllamaReview(
                        "MODEL_COOLDOWN", {}, ("persistent circuit breaker is open",)
                    )
    if cached:
        return OllamaReview(
            str(cached["validation_status"]), dict(cached["parsed_response"] or {}),
            tuple(cached["validation_errors"] or []), True,
        )

    raw = ""
    data: dict[str, Any] = {}
    errors: tuple[str, ...] = ()
    status = "VALID"
    try:
        response = requests.post(
            setting("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/") + "/api/chat",
            json={
                "model": model, "stream": False, "format": "json", "think": False,
                "messages": [{"role": "user", "content": _prompt(
                    source_type, institution, directory_url, source_text
                )}],
                "keep_alive": "10m",
                "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 700},
            },
            timeout=setting_int("OLLAMA_TIMEOUT_SECONDS", 300, 10, 300),
        )
        response.raise_for_status()
        payload = response.json()
        raw = str((payload.get("message") or {}).get("content") or payload.get("response") or "")
        data = _clean_json(raw)
        errors = validate_review(data, source_text)
        status = "VALID" if not errors else "INVALID_EVIDENCE"
    except requests.RequestException as error:
        status, errors = "MODEL_UNAVAILABLE", (type(error).__name__,)
        _unavailable_until = time.monotonic() + cooldown_seconds
    except (ValueError, json.JSONDecodeError) as error:
        status, errors = "INVALID_RESPONSE", (type(error).__name__,)

    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO ollama_extraction_runs
                   (source_type,source_record_key,institution_id,model,prompt_version,
                    input_hash,input_excerpt,raw_response,parsed_response,
                    validation_status,validation_errors)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
                   ON CONFLICT (source_type,source_record_key,model,prompt_version,input_hash)
                   DO UPDATE SET raw_response=EXCLUDED.raw_response,
                     parsed_response=EXCLUDED.parsed_response,
                     validation_status=EXCLUDED.validation_status,
                     validation_errors=EXCLUDED.validation_errors, created_at=NOW()""",
                (source_type, source_record_key, institution_id, model, PROMPT_VERSION,
                 input_hash, source_text[:24000], raw[:24000], json.dumps(data),
                 status, list(errors)),
            )
    return OllamaReview(status, data, errors)


def review_publication_identity(
    *, source_record_key: str, institution_id: int, professor_name: str,
    institution: str, department: str, official_email_domain: str,
    known_pages: list[str], scholar_profile: dict[str, Any],
) -> OllamaReview:
    """Ask Qwen to structure one Scholar candidate; code makes the decision."""
    papers = scholar_profile.get("papers") or []
    source = {
        "official_professor_name": professor_name,
        "official_institution": institution,
        "official_department": department,
        "official_email_domain": official_email_domain,
        "official_profile_or_personal_pages": known_pages,
        "scholar_profile_name": scholar_profile.get("name", ""),
        "scholar_verified_email": scholar_profile.get("verified_email", ""),
        "scholar_affiliation": scholar_profile.get("affiliation", ""),
        "scholar_homepage": scholar_profile.get("homepage", ""),
        "scholar_research_interests": scholar_profile.get("research_interests", []),
        "representative_papers": [
            {"title": getattr(p, "title", ""), "authors": getattr(p, "authors", ""),
             "venue": getattr(p, "venue", ""), "year": getattr(p, "year", None)}
            for p in papers[:12]
        ],
    }
    source_text = json.dumps(source, ensure_ascii=False)
    prompt = f"""Review whether ONE Google Scholar profile can belong to ONE already verified faculty member.
Do not establish faculty employment. Do not guess. The supplied profile data is
stored separately, so do not repeat names, pages, interests, or papers.
Return one concise JSON object only:
{{"same_person":"YES|NO|UNCLEAR","matching_signals":["short signal"],
"conflicts":["short conflict"],"confidence":0.0}}
A name match alone is insufficient. Keep the whole answer below 300 tokens.
SOURCE_JSON:
{source_text}
"""
    return _run_cached_review(
        source_type="SCHOLAR_IDENTITY", source_record_key=source_record_key,
        institution_id=institution_id, prompt_version=PUBLICATION_PROMPT_VERSION,
        source_text=source_text, prompt=prompt,
    )


def review_research_interest_summary(
    *, source_record_key: str, institution_id: int, professor_name: str,
    institution: str, department: str, biography_text: str,
    explicit_interests: list[str] | None = None, speculative: bool = False,
) -> OllamaReview:
    """Summarize broad interests from one verified subject-local biography.

    This review cannot establish employment, publications, or research-area
    membership. Callers validate and store the result as interest-only evidence.
    """
    source = {
        "professor_name": professor_name,
        "institution": institution,
        "department": department,
        "biography": biography_text[:16000],
        "explicit_interests": explicit_interests or [],
        "speculative": speculative,
    }
    source_text = json.dumps(source, ensure_ascii=False)
    prompt = f"""Summarize the research or professional subject areas of ONE already
verified faculty member from the supplied biography. Return broad noun phrases,
not claims about publications. Do not invent a field that is not supported by
the biography. Teaching, administration, employers, awards, and hobbies are not
research interests unless the biography explicitly connects them to scholarly
work. Return JSON only:
{{"research_interests":["2 to 8 word label"],
  "basis_summary":"one short explanation",
  "confidence":0.0}}
Return at most 8 unique labels. Return an empty list when the biography does not
support any research or scholarly interests.
SOURCE_JSON:
{source_text}
"""
    if explicit_interests:
        prompt += "\nValidate and normalize the explicit labels against the excerpt."
    if speculative:
        prompt = (
            "Suggest at most 5 broad POSSIBLE academic fields from the department. "
            "These are unsupported AI suggestions, not known interests of this person. "
            "Never infer fields from their name, ethnicity, gender or school reputation. "
            "If department is absent or vague return an empty list. Return JSON only: "
            '{"research_interests":[],"basis_summary":"","confidence":0.1}. '
            + source_text
        )
    review = _run_cached_review(
        source_type="RESEARCH_INTEREST_SUMMARY",
        source_record_key=source_record_key,
        institution_id=institution_id,
        prompt_version=RESEARCH_INTEREST_PROMPT_VERSION,
        source_text=source_text,
        prompt=prompt,
    )
    if review.status != "VALID":
        return review
    labels = review.data.get("research_interests")
    errors: list[str] = []
    if not isinstance(labels, list):
        errors.append("research_interests must be a list")
    else:
        if len(labels) > 8:
            errors.append("at most 8 labels are permitted")
        for label in labels:
            words = str(label or "").split()
            if not isinstance(label, str) or not 2 <= len(label.strip()) <= 80:
                errors.append("each research interest must be 2-80 characters")
            elif len(words) > 8:
                errors.append("each research interest must contain at most 8 words")
    if errors:
        return OllamaReview("INVALID_EVIDENCE", review.data, tuple(errors), review.cached)
    return review


def _run_cached_review(*, source_type: str, source_record_key: str,
                       institution_id: int, prompt_version: str,
                       source_text: str, prompt: str) -> OllamaReview:
    """Cached JSON-only model call used by non-roster evidence reviewers."""
    global _unavailable_until
    if not enabled():
        return OllamaReview("DISABLED", {})
    model = setting("OLLAMA_MODEL", "qwen2.5-coder:7b").strip()
    input_hash = hashlib.sha256(source_text.encode("utf-8", "replace")).hexdigest()
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""SELECT parsed_response,validation_status,validation_errors
                FROM ollama_extraction_runs WHERE source_type=%s AND source_record_key=%s
                AND model=%s AND prompt_version=%s AND input_hash=%s""",
                (source_type, source_record_key, model, prompt_version, input_hash))
            cached = cursor.fetchone()
    if cached and cached['validation_status'] != 'MODEL_UNAVAILABLE':
        return OllamaReview(str(cached["validation_status"]), dict(cached["parsed_response"] or {}),
                            tuple(cached["validation_errors"] or []), True)
    raw, data, errors, status = "", {}, (), "VALID"
    try:
        response = requests.post(
            setting("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/") + "/api/chat",
            json={"model": model, "stream": False, "format": "json", "think": False,
                  "messages": [{"role": "user", "content": prompt}], "keep_alive": "10m",
                  "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 400}},
            timeout=setting_int("OLLAMA_TIMEOUT_SECONDS", 300, 10, 300))
        response.raise_for_status()
        raw = str((response.json().get("message") or {}).get("content") or "")
        data = _clean_json(raw)
    except requests.RequestException as error:
        status, errors = "MODEL_UNAVAILABLE", (type(error).__name__,)
    except (ValueError, json.JSONDecodeError) as error:
        status, errors = "INVALID_RESPONSE", (type(error).__name__,)
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""INSERT INTO ollama_extraction_runs
                (source_type,source_record_key,institution_id,model,prompt_version,input_hash,
                 input_excerpt,raw_response,parsed_response,validation_status,validation_errors)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
                ON CONFLICT (source_type,source_record_key,model,prompt_version,input_hash)
                DO UPDATE SET raw_response=EXCLUDED.raw_response,parsed_response=EXCLUDED.parsed_response,
                validation_status=EXCLUDED.validation_status,validation_errors=EXCLUDED.validation_errors,
                created_at=NOW()""",
                (source_type, source_record_key, institution_id, model, prompt_version, input_hash,
                 source_text[:24000], raw[:24000], json.dumps(data), status, list(errors)))
    return OllamaReview(status, data, errors)
