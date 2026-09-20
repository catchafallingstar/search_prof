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
PUBLICATION_PROMPT_VERSION = "publication-identity-v3"
RESEARCH_INTEREST_PROMPT_VERSION = "research-interest-v3"
PAPER_RESEARCH_PROMPT_VERSION = "paper-research-v4"
SCHOLAR_PUBLICATION_FILTER_PROMPT_VERSION = "scholar-publication-filter-v1"
ALLOWED_RECORD_TYPES = {
    "FACULTY", "EMERITUS", "ADJUNCT", "VISITING", "RESEARCH_FACULTY",
    "LECTURER", "INSTRUCTOR", "STUDENT", "POSTDOC", "STAFF",
    "ADMINISTRATOR", "OTHER", "UNCLEAR",
}
NONFACULTY_TYPES = {"STUDENT", "POSTDOC", "STAFF", "ADMINISTRATOR", "OTHER"}

# Structured Qwen calls need enough output and total context budget to finish
# one complete JSON object. The paper-area reviewer can return several exact
# paper titles, so the old 400-token output cap was too small and could cut a
# response off mid-object. One immediate repair retry is allowed only for
# malformed/truncated JSON; evidence-validation failures are not retried.
OLLAMA_JSON_NUM_CTX = 8192
OLLAMA_JSON_NUM_PREDICT = 1900
OLLAMA_INVALID_JSON_RETRIES = 1
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


def _request_json_object(*, model: str, prompt: str) -> tuple[str, dict[str, Any], tuple[str, ...]]:
    """Request one complete JSON object, retrying malformed output once.

    Ollama already runs in JSON mode, but a response can still be incomplete if
    generation reaches an output/context limit.  A single immediate repair
    retry uses the same evidence and a stricter completion instruction.  If
    that retry also fails, callers receive INVALID_RESPONSE and can route the
    evidence to staff review instead of looping indefinitely.
    """
    last_raw = ""
    last_error = "INVALID_RESPONSE"
    retry_suffix = (
        "\n\nIMPORTANT JSON REPAIR RETRY: The previous model response was incomplete "
        "or invalid JSON. Return exactly ONE complete JSON object and nothing "
        "else. Do not use Markdown or code fences. Keep the response concise. "
        "Close every string, array, and object. Do not repeat the source input "
        "outside the requested JSON fields."
    )
    for attempt in range(OLLAMA_INVALID_JSON_RETRIES + 1):
        attempt_prompt = prompt if attempt == 0 else prompt + retry_suffix
        response = requests.post(
            setting("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/") + "/api/chat",
            json={
                "model": model,
                "stream": False,
                "format": "json",
                "think": False,
                "messages": [{"role": "user", "content": attempt_prompt}],
                "keep_alive": "10m",
                "options": {
                    "temperature": 0,
                    "num_ctx": OLLAMA_JSON_NUM_CTX,
                    "num_predict": OLLAMA_JSON_NUM_PREDICT,
                },
            },
            timeout=setting_int("OLLAMA_TIMEOUT_SECONDS", 300, 10, 300),
        )
        response.raise_for_status()
        payload = response.json()
        last_raw = str(
            (payload.get("message") or {}).get("content")
            or payload.get("response")
            or ""
        )
        done_reason = str(payload.get("done_reason") or "").casefold()
        try:
            if done_reason in {"length", "max_tokens"}:
                raise ValueError("Ollama output was truncated before JSON completed")
            return last_raw, _clean_json(last_raw), ()
        except (ValueError, json.JSONDecodeError) as error:
            last_error = (
                "OUTPUT_TRUNCATED"
                if done_reason in {"length", "max_tokens"}
                else type(error).__name__
            )
            if attempt < OLLAMA_INVALID_JSON_RETRIES:
                continue
    return last_raw, {}, (last_error, "JSON_RETRY_EXHAUSTED")


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
    if cached and cached["validation_status"] not in {
        "MODEL_UNAVAILABLE", "INVALID_RESPONSE"
    }:
        return OllamaReview(
            str(cached["validation_status"]), dict(cached["parsed_response"] or {}),
            tuple(cached["validation_errors"] or []), True,
        )

    raw = ""
    data: dict[str, Any] = {}
    errors: tuple[str, ...] = ()
    status = "VALID"
    try:
        raw, data, response_errors = _request_json_object(
            model=model,
            prompt=_prompt(source_type, institution, directory_url, source_text),
        )
        if response_errors:
            status, errors = "INVALID_RESPONSE", response_errors
        else:
            errors = validate_review(data, source_text)
            status = "VALID" if not errors else "INVALID_EVIDENCE"
    except requests.RequestException as error:
        status, errors = "MODEL_UNAVAILABLE", (type(error).__name__,)
        _unavailable_until = time.monotonic() + cooldown_seconds

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


def _normalized_evidence_text(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def review_publication_identity(
    *, source_record_key: str, institution_id: int, professor_name: str,
    institution: str, department: str, official_email_domain: str,
    known_pages: list[str], scholar_profile: dict[str, Any],
    official_biography: str = "", official_interests: list[str] | None = None,
) -> OllamaReview:
    """Correlate one Scholar profile with already verified official evidence.

    Qwen may provide a second identity signal, but only when it returns
    source-grounded evidence from both the official faculty record and the
    Scholar record. A name match by itself can never verify a profile.
    """
    papers = scholar_profile.get("papers") or []
    source = {
        "official_professor_name": professor_name,
        "official_institution": institution,
        "official_department": department,
        "official_email_domain": official_email_domain,
        "official_profile_or_personal_pages": known_pages,
        "official_research_interests": official_interests or [],
        "official_biography": official_biography[:12000],
        "scholar_profile_name": scholar_profile.get("name", ""),
        "scholar_verified_email": scholar_profile.get("verified_email", ""),
        "scholar_affiliation": scholar_profile.get("affiliation", ""),
        "scholar_homepage": scholar_profile.get("homepage", ""),
        "scholar_research_interests": scholar_profile.get("research_interests", []),
        "representative_papers": [
            {"title": getattr(p, "title", ""), "authors": getattr(p, "authors", ""),
             "venue": getattr(p, "venue", ""), "year": getattr(p, "year", None)}
            for p in papers[:20]
        ],
    }
    source_text = json.dumps(source, ensure_ascii=False)
    prompt = f"""Review whether ONE Google Scholar profile belongs to ONE already verified faculty member.
Do not establish employment and do not guess. A compatible name alone is never
enough. Correlate independent facts such as affiliation, verified email domain,
homepage, official research interests/biography, and the subjects of representative
papers. Evidence must be copied exactly from SOURCE_JSON values.

Return one concise JSON object only:
{{"same_person":"YES|NO|UNCLEAR",
  "official_evidence":["exact official-side text"],
  "scholar_evidence":["exact Scholar-side text or exact paper title"],
  "matching_signals":["short explanation"],
  "conflicts":["short conflict"],
  "confidence":0.0}}

For YES, include at least one non-name official evidence item and at least one
non-name Scholar evidence item. If that cannot be done, return UNCLEAR.
SOURCE_JSON:
{source_text}
"""
    review = _run_cached_review(
        source_type="SCHOLAR_IDENTITY", source_record_key=source_record_key,
        institution_id=institution_id, prompt_version=PUBLICATION_PROMPT_VERSION,
        source_text=source_text, prompt=prompt,
    )
    if review.status != "VALID":
        return review

    data = dict(review.data)
    errors: list[str] = []
    same_person = str(data.get("same_person") or "").upper()
    if same_person not in {"YES", "NO", "UNCLEAR"}:
        errors.append("same_person must be YES, NO, or UNCLEAR")

    official_evidence = data.get("official_evidence") or []
    scholar_evidence = data.get("scholar_evidence") or []
    if not isinstance(official_evidence, list):
        errors.append("official_evidence must be a list")
        official_evidence = []
    if not isinstance(scholar_evidence, list):
        errors.append("scholar_evidence must be a list")
        scholar_evidence = []
    if not isinstance(data.get("matching_signals", []), list):
        errors.append("matching_signals must be a list")
    if not isinstance(data.get("conflicts", []), list):
        errors.append("conflicts must be a list")

    try:
        confidence = float(data.get("confidence") or 0)
        if not 0 <= confidence <= 1:
            raise ValueError
    except (TypeError, ValueError):
        confidence = 0.0
        errors.append("confidence must be between 0 and 1")

    # For a model-only promotion, require subject-specific official evidence.
    # Generic institution names, domains, and profile URLs are provenance, not
    # enough to correlate a name-only Scholar candidate.
    official_text = _normalized_evidence_text(" ".join([
        department, *(official_interests or []), official_biography,
    ]))
    scholar_parts = [
        scholar_profile.get("affiliation", ""), scholar_profile.get("verified_email", ""),
        scholar_profile.get("homepage", ""), *(scholar_profile.get("research_interests") or []),
    ]
    for paper in papers[:20]:
        scholar_parts.extend([
            getattr(paper, "title", ""), getattr(paper, "authors", ""),
            getattr(paper, "venue", ""),
        ])
    scholar_text = _normalized_evidence_text(" ".join(str(v or "") for v in scholar_parts))
    normalized_name = _normalized_evidence_text(professor_name)

    valid_official = []
    for value in official_evidence:
        normalized = _normalized_evidence_text(value)
        if not normalized or normalized == normalized_name or normalized not in official_text:
            errors.append("official_evidence is not grounded in official source data")
        else:
            valid_official.append(str(value))
    valid_scholar = []
    for value in scholar_evidence:
        normalized = _normalized_evidence_text(value)
        if not normalized or normalized == normalized_name or normalized not in scholar_text:
            errors.append("scholar_evidence is not grounded in Scholar source data")
        else:
            valid_scholar.append(str(value))

    if same_person == "YES" and (not valid_official or not valid_scholar):
        errors.append("YES requires grounded evidence from both official and Scholar data")

    data["same_person"] = same_person
    data["official_evidence"] = valid_official
    data["scholar_evidence"] = valid_scholar
    data["confidence"] = confidence
    if errors:
        return OllamaReview("INVALID_EVIDENCE", data, tuple(dict.fromkeys(errors)), review.cached)
    return OllamaReview("VALID", data, (), review.cached)



def review_scholar_publication_candidates(
    *, source_record_key: str, institution_id: int,
    candidates: list[dict[str, Any]],
) -> OllamaReview:
    """Classify only ambiguous Scholar rows as publication/service/uncertain.

    Deterministic rules handle obvious rows before this function is called.
    The model receives only row metadata and cannot establish authorship or
    professor identity. Low-confidence answers remain manual-review items.
    """
    clean: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates[:25], 1):
        candidate_id = str(candidate.get("candidate_id") or index)
        title = " ".join(str(candidate.get("title") or "").split())
        if not title:
            continue
        clean.append({
            "candidate_id": candidate_id,
            "title": title,
            "authors": " ".join(str(candidate.get("authors") or "").split()),
            "venue": " ".join(str(candidate.get("venue") or "").split()),
            "year": candidate.get("year"),
        })
    if not clean:
        return OllamaReview("INVALID_EVIDENCE", {}, ("no Scholar candidates supplied",))

    source_text = json.dumps({"rows": clean}, ensure_ascii=False)
    prompt = f"""Classify each Google Scholar row using ONLY the supplied metadata.
A PUBLICATION is a scholarly work such as a journal/conference paper, book
chapter, preprint, report, proceedings/editorial item, or other authored
scholarly output. NOT_PUBLICATION means service/event metadata such as program
committee membership, workshop organization, chair listings, a bare event name,
people/affiliation lists, or corrupted non-publication metadata. If the metadata
is genuinely insufficient or could reasonably be either, use UNCERTAIN.

Do not infer from a person's reputation, institution, or field. Keep each
candidate_id exactly unchanged. Return JSON only:
{{"items":[{{"candidate_id":"1","decision":"PUBLICATION|NOT_PUBLICATION|UNCERTAIN","confidence":0.0,"reason":"short reason"}}]}}
Return exactly one item for every supplied row, no extra rows, confidence 0 to 1,
and no text outside the JSON object.
SOURCE_JSON:
{source_text}
"""
    review = _run_cached_review(
        source_type="SCHOLAR_PUBLICATION_FILTER",
        source_record_key=source_record_key,
        institution_id=institution_id,
        prompt_version=SCHOLAR_PUBLICATION_FILTER_PROMPT_VERSION,
        source_text=source_text,
        prompt=prompt,
    )
    if review.status != "VALID":
        return review

    items = review.data.get("items")
    if not isinstance(items, list):
        return OllamaReview(
            "INVALID_EVIDENCE", review.data, ("items must be a list",), review.cached
        )

    expected = {item["candidate_id"] for item in clean}
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    errors: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            errors.append("each item must be an object")
            continue
        candidate_id = str(item.get("candidate_id") or "")
        decision = str(item.get("decision") or "").upper()
        try:
            confidence = float(item.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = -1
        if candidate_id not in expected or candidate_id in seen:
            errors.append("candidate_id must match one supplied row exactly once")
            continue
        seen.add(candidate_id)
        if decision not in {"PUBLICATION", "NOT_PUBLICATION", "UNCERTAIN"}:
            errors.append("decision must be PUBLICATION, NOT_PUBLICATION, or UNCERTAIN")
            continue
        if not 0 <= confidence <= 1:
            errors.append("confidence must be between 0 and 1")
            continue
        normalized.append({
            "candidate_id": candidate_id,
            "decision": decision,
            "confidence": confidence,
            "reason": " ".join(str(item.get("reason") or "").split())[:300],
        })
    if seen != expected:
        errors.append("model must return exactly one decision for every supplied row")
    data = dict(review.data)
    data["items"] = normalized
    if errors:
        return OllamaReview(
            "INVALID_EVIDENCE", data, tuple(dict.fromkeys(errors)), review.cached
        )
    return OllamaReview("VALID", data, (), review.cached)

def review_paper_research_summary(
    *, source_record_key: str, institution_id: int, professor_name: str,
    institution: str, papers: list[dict[str, Any]],
    scholar_interests: list[str] | None = None,
) -> OllamaReview:
    """Summarize broad research areas from already identity-verified papers.

    Every returned area must cite exact paper titles from the supplied set so
    callers can audit the model output. This function never verifies identity.
    """
    clean_papers = []
    for paper in papers[:30]:
        title = " ".join(str(paper.get("title") or "").split())
        if not title:
            continue
        clean_papers.append({
            "title": title,
            "year": paper.get("year"),
            "venue": " ".join(str(paper.get("venue") or "").split()),
        })
    if not clean_papers:
        return OllamaReview("INVALID_EVIDENCE", {}, ("no paper titles supplied",))

    source = {
        "professor_name": professor_name,
        "institution": institution,
        "scholar_self_listed_interests": scholar_interests or [],
        "verified_papers": clean_papers,
    }
    source_text = json.dumps(source, ensure_ascii=False)
    prompt = f"""Infer broad CURRENT OR ESTABLISHED research areas for ONE already
identity-verified faculty member using only the supplied verified paper titles,
venues, years, and optional Scholar self-listed interests. Do not infer from the
person's name or university. Prefer stable subject areas over one-off methods.

Return JSON only:
{{"primary_field":"1 to 6 word broad academic field",
  "research_areas":[
    {{"label":"2 to 8 word broad research area",
      "supporting_titles":["exact supplied paper title","exact supplied paper title"]}}
  ],
  "basis_summary":"one short explanation"}}

Return at most 8 areas. When at least two papers are supplied, use exactly two
different exact supplied paper titles per area; use one only when the entire input
has one paper. Do not invent or paraphrase titles. Keep basis_summary under 30
words and return no commentary outside the JSON object.
SOURCE_JSON:
{source_text}
"""
    review = _run_cached_review(
        source_type="PAPER_RESEARCH_SUMMARY", source_record_key=source_record_key,
        institution_id=institution_id, prompt_version=PAPER_RESEARCH_PROMPT_VERSION,
        source_text=source_text, prompt=prompt,
    )
    if review.status != "VALID":
        return review

    data = dict(review.data)
    primary_field = " ".join(str(data.get("primary_field") or "").split())
    errors: list[str] = []
    if primary_field and (len(primary_field) > 80 or len(primary_field.split()) > 6):
        errors.append("primary_field must be at most 80 characters and 6 words")
        primary_field = ""
    data["primary_field"] = primary_field
    areas = data.get("research_areas")
    if not isinstance(areas, list):
        return OllamaReview("INVALID_EVIDENCE", data, ("research_areas must be a list",), review.cached)
    if len(areas) > 8:
        errors.append("at most 8 research areas are permitted")

    title_lookup = {
        _normalized_evidence_text(item["title"]): item["title"]
        for item in clean_papers
    }
    normalized_areas: list[dict[str, Any]] = []
    for area in areas[:8]:
        if not isinstance(area, dict):
            errors.append("each research area must be an object")
            continue
        label = " ".join(str(area.get("label") or "").split())
        titles = area.get("supporting_titles") or []
        if not 2 <= len(label) <= 80 or len(label.split()) > 8:
            errors.append("research area labels must be 2-80 characters and at most 8 words")
            continue
        if not isinstance(titles, list):
            errors.append("supporting_titles must be a list")
            continue
        grounded: list[str] = []
        for title in titles:
            match = title_lookup.get(_normalized_evidence_text(title))
            if match and match not in grounded:
                grounded.append(match)
            else:
                errors.append("supporting title is not an exact supplied paper title")
        required = 2 if len(clean_papers) >= 2 else 1
        if len(grounded) < required:
            errors.append("each research area requires enough distinct supporting papers")
            continue
        normalized_areas.append({"label": label, "supporting_titles": grounded[:5]})

    if not normalized_areas and areas:
        errors.append("no research areas had valid paper support")

    data["research_areas"] = normalized_areas
    data["research_interests"] = [
        item["label"] for item in normalized_areas
    ]
    data["supporting_evidence"] = {
        item["label"]: item["supporting_titles"]
        for item in normalized_areas
    }

    unique_errors = tuple(dict.fromkeys(errors))

    # Validate each proposed research area independently. A malformed or
    # unsupported area must never invalidate other areas whose exact paper-title
    # evidence has already passed validation. Keep warnings for auditing.
    if normalized_areas:
        if unique_errors:
            data["validation_warnings"] = list(unique_errors)
        return OllamaReview("VALID", data, (), review.cached)

    # If Qwen proposed areas but none survived evidence validation, do not
    # invent a profile: route the saved paper evidence to staff review.
    if unique_errors:
        return OllamaReview(
            "INVALID_EVIDENCE", data, unique_errors, review.cached
        )

    # An intentionally empty area list is valid and becomes NO_SUPPORTED_AREAS
    # at the caller rather than fabricated research evidence.
    return OllamaReview("VALID", data, (), review.cached)

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
    if speculative:
        return OllamaReview(
            "INVALID_EVIDENCE", {},
            ("unsupported department-only research inference is disabled",),
        )
    prompt = f"""Summarize the research or professional subject areas of ONE already
verified faculty member from the supplied biography. Return broad noun phrases,
not claims about publications. Do not invent a field that is not supported by
the biography. Teaching, administration, employers, awards, and hobbies are not
research interests unless the biography explicitly connects them to scholarly
work. Return JSON only:
{{"primary_field":"1 to 6 word broad academic field",
  "research_interests":["2 to 8 word label"],
  "basis_summary":"one short explanation",
  "confidence":0.0}}
Return at most 8 unique labels. Return an empty list when the biography does not
support any research or scholarly interests.
SOURCE_JSON:
{source_text}
"""
    if explicit_interests:
        prompt += "\nValidate and normalize the explicit labels against the excerpt."
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
        labels = []
    else:
        labels = labels[:8]
        for label in labels:
            words = str(label or "").split()
            if not isinstance(label, str) or not 2 <= len(label.strip()) <= 80:
                errors.append("each research interest must be 2-80 characters")
            elif len(words) > 8:
                errors.append("each research interest must contain at most 8 words")
    if errors:
        return OllamaReview("INVALID_EVIDENCE", review.data, tuple(errors), review.cached)
    data = dict(review.data)
    primary_field = " ".join(str(data.get("primary_field") or "").split())
    if primary_field and (len(primary_field) > 80 or len(primary_field.split()) > 6):
        return OllamaReview(
            "INVALID_EVIDENCE", data,
            ("primary_field must be at most 80 characters and 6 words",), review.cached,
        )
    data["primary_field"] = primary_field
    data["research_interests"] = labels
    return OllamaReview("VALID", data, (), review.cached)


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
    if cached and cached["validation_status"] not in {
        "MODEL_UNAVAILABLE", "INVALID_RESPONSE"
    }:
        return OllamaReview(
            str(cached["validation_status"]), dict(cached["parsed_response"] or {}),
            tuple(cached["validation_errors"] or []), True,
        )
    raw, data, errors, status = "", {}, (), "VALID"
    try:
        raw, data, response_errors = _request_json_object(
            model=model, prompt=prompt,
        )
        if response_errors:
            status, errors = "INVALID_RESPONSE", response_errors
    except requests.RequestException as error:
        status, errors = "MODEL_UNAVAILABLE", (type(error).__name__,)
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
