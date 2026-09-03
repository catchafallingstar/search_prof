"""Paced, non-publishing diagnostic: 30 due US candidates, a bounded pass each."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from unittest.mock import patch

from db import get_db_connection
from identity_schedule import minimum_recheck_sql
from ingestion import verify_faculty as verifier
from ingestion.search_budget import provider_capacity
from ingestion.websearch import search_web, SearchUnavailable, _provider_names
from settings import setting_int
from radar_store import fetch_radar_topic


def _compact_text(value, limit: int = 900) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _historical_attributions(text: str) -> list[dict]:
    """Extract explicit historical education claims for diagnostic display.

    These rows explain identity continuity; they never establish a current
    faculty appointment. Keep the quoted sentence so a human can audit the
    extraction instead of trusting the label alone.
    """
    attributions = []
    normalized_text = _compact_text(text, 4000)
    claim_pattern = re.compile(
        r"\b(?:earned|received|completed|obtained|holds?)\b.{0,120}?"
        r"\b(?P<degree>Ph\.?D\.?|doctoral\s+degree|doctorate|master(?:'s)?\s+degree|M\.?S\.?)\b"
        r".{0,140}?\b(?:from|at)\s+(?P<institution>"
        r"(?:the\s+)?(?:University|College|Institute|School)[^,;.!?]{1,100})",
        re.IGNORECASE,
    )
    # Search the full text rather than splitting on periods: abbreviations such
    # as "Ph.D." otherwise look like sentence endings.
    for claim in claim_pattern.finditer(normalized_text):
        degree = claim.group("degree")
        observed_role = (
            "PhD graduate"
            if re.search(r"ph\.?d\.?|doctoral|doctorate", degree, re.I)
            else "Master's graduate"
        )
        attributions.append({
            "observed_role": observed_role,
            "role_category": "EDUCATION",
            "observed_institution": _compact_text(claim.group("institution"), 140),
            "observed_employer": None,
            "currentness": "HISTORICAL",
            "evidence": _compact_text(claim.group(0)),
        })
    return attributions


def useful_sources(decision: dict) -> list[dict]:
    """Return a readable, source-by-source attribution trail for JSON/Markdown."""
    sources = []
    for row in verifier._identity_evidence_rows(decision):
        url = str(row.get("source_url") or "").strip()
        if not url:
            continue
        source_type = str(row.get("source_type") or "SEARCH_RESULT").upper()
        excerpt = _compact_text(
            row.get("evidence_excerpt")
            or row.get("evidence_text")
            or row.get("extracted_text")
            or row.get("snippet")
        )
        role = row.get("observed_title") or row.get("role")
        role_category = str(row.get("role_category") or "UNKNOWN").upper()
        institution = row.get("observed_institution")
        if not institution and role_category in {"FACULTY", "STUDENT", "POSTDOC"}:
            found = verifier.institutions_in_text(excerpt)
            institution = found[0] if found else row.get("institution_name")
        attribution = {
            "observed_role": _compact_text(role, 160) or None,
            "role_category": role_category,
            "observed_institution": _compact_text(institution, 180) or None,
            "observed_employer": _compact_text(row.get("observed_employer"), 180) or None,
            "currentness": str(row.get("currentness") or "UNKNOWN").upper(),
            "evidence": excerpt or None,
        }
        attributions = []
        if any(attribution[key] for key in (
            "observed_role", "observed_institution", "observed_employer", "evidence"
        )):
            attributions.append(attribution)
        historical_text = " ".join(str(row.get(key) or "") for key in (
            "extracted_text", "evidence_text", "evidence_excerpt", "snippet"
        ))
        for historical in _historical_attributions(historical_text):
            if not any(
                item.get("currentness") == "HISTORICAL"
                and item.get("observed_role") == historical["observed_role"]
                and item.get("observed_institution") == historical["observed_institution"]
                for item in attributions
            ):
                attributions.append(historical)
        useful_type = source_type in {
            "OFFICIAL_UNIVERSITY_PAGE", "PERSONAL_WEBSITE", "LAB_WEBSITE",
            "CV", "LINKEDIN_SNIPPET", "GOOGLE_SCHOLAR",
        }
        if not (attributions and (useful_type or row.get("supports_decision") or role_category != "UNKNOWN")):
            continue
        sources.append({
            "source_type": source_type,
            "url": url,
            "page_title": _compact_text(row.get("page_title"), 220) or None,
            "lookup_status": str(row.get("lookup_status") or "FOUND").upper(),
            "inspection_status": str(row.get("inspection_status") or "UNKNOWN").upper(),
            "supports_decision": bool(row.get("supports_decision")),
            "attributions": attributions,
        })
    return sources


def render_markdown_report(results: list[dict], attempts_label: str,
                           max_searches_per_candidate: int) -> str:
    lines = [
        "# Faculty verification diagnostic", "",
        f"Candidates: {len(results)} · Outbound attempts: {attempts_label}", "",
        f"Up to {max_searches_per_candidate} queries per candidate. "
        "No identity decisions were published or saved.", "",
        "| Candidate | Field | Outcome | Useful sources |", "|---|---|---|---|",
    ]
    for row in results:
        name = str(row.get("name") or "Unknown").replace("|", "/")
        field = str(row.get("field") or "Unassigned").replace("|", "/")
        lines.append(
            f"| {name} | {field} | {row.get('status')} | "
            f"{len(row.get('useful_sources') or [])} |"
        )
    for row in results:
        lines.extend([
            "", f"## {row.get('index')}. {row.get('name')} — {row.get('status')}", "",
            f"- Field: {row.get('field') or 'Unassigned'}",
            f"- Imported university: {row.get('imported_university') or 'Not available'}",
            f"- Decision method: {row.get('method') or 'Not available'}",
            f"- Failure code: {row.get('failure_code') or 'None'}",
            f"- Reason: {_compact_text(row.get('reason'), 1000)}", "",
            "### Useful identity sources", "",
        ])
        sources = row.get("useful_sources") or []
        if not sources:
            lines.append("No useful identity source was retained in this bounded pass.")
            continue
        for source_index, source in enumerate(sources, 1):
            lines.extend([
                f"#### Source {source_index}: {source['source_type'].replace('_', ' ').title()}", "",
                f"- URL: {source['url']}",
                f"- Page title: {source.get('page_title') or 'Not available'}",
                f"- Lookup status: {source.get('lookup_status') or 'UNKNOWN'}",
                f"- Source handling: {'Opened and evaluated' if source.get('inspection_status') == 'INSPECTED' else 'Returned by search only'}",
                "- Used in assessment: Yes",
                f"- Proves final classification: {'Yes' if source.get('supports_decision') else 'No'}",
            ])
            for attribution_index, attribution in enumerate(source.get("attributions") or [], 1):
                lines.extend([
                    "", f"Attribution {attribution_index}:", "",
                    f"- Observed role: {attribution.get('observed_role') or 'Not stated'}",
                    f"- Role category: {attribution.get('role_category') or 'UNKNOWN'}",
                    f"- Observed institution: {attribution.get('observed_institution') or 'Not stated'}",
                    f"- Observed employer: {attribution.get('observed_employer') or 'Not stated'}",
                    f"- Currentness: {attribution.get('currentness') or 'UNKNOWN'}",
                    f"> {attribution.get('evidence') or 'No text excerpt retained.'}",
                ])
    return "\n".join(lines) + "\n"


def candidates(limit: int, random_seed: int | None = None, excluded_ids: list[int] | None = None,
               replay_ids: list[int] | None = None, field: str = '', include_recent: bool = False) -> list[dict]:
    if field and replay_ids is not None:
        raise ValueError('Choose either a field sample or a replay report, not both.')
    topic = fetch_radar_topic(field) if field else None
    if field and not topic:
        raise ValueError(f'No existing research index for {field!r}; this diagnostic does not launch OpenAlex discovery.')
    ordering = ('md5(id::text || %s)' if random_seed is not None else
                'ROW_NUMBER() OVER (PARTITION BY sample_field ORDER BY id), id')
    parameters = [excluded_ids or []]
    schedule = "p.faculty_status IS DISTINCT FROM 'CONFLICT'" if include_recent else (
        minimum_recheck_sql() + ' AND (p.next_identity_check_at IS NULL OR p.next_identity_check_at <= NOW())')
    eligibility = f"""i.country_code = 'US' AND {schedule}
                      AND p.faculty_verification_method IS DISTINCT FROM 'manual_review'
                      AND EXISTS (SELECT 1 FROM professor_papers pp WHERE pp.professor_id = p.id)
                      AND EXISTS (SELECT 1 FROM radar_topic_professors rtp WHERE rtp.professor_id = p.id AND rtp.is_current_match)
                      AND NOT (p.id = ANY(%s))"""
    if topic:
        eligibility += ''' AND EXISTS (
            SELECT 1 FROM radar_topic_professor_papers e
            JOIN radar_topic_professors rtp ON rtp.professor_id = e.professor_id
                                         AND rtp.radar_topic_id = e.radar_topic_id
            WHERE e.professor_id = p.id AND e.radar_topic_id = %s
              AND e.is_current_match AND rtp.is_current_match)'''
        parameters.append(topic['id'])
    if random_seed is not None:
        parameters.append(str(random_seed))
    parameters.append(limit)
    if replay_ids is not None:
        # Diagnostic only: repeat these exact IDs even if the worker checked
        # them since the earlier sample. No identity decisions are written.
        eligibility = 'p.id = ANY(%s)'
        ordering = 'array_position(%s::integer[], id::integer)'
        parameters = [replay_ids, replay_ids, limit]
    field_label = """COALESCE((SELECT t.requested_query FROM radar_topic_professors rtp
                            JOIN radar_topics t ON t.id = rtp.radar_topic_id
                            WHERE rtp.professor_id = p.id AND rtp.is_current_match
                            ORDER BY rtp.result_rank LIMIT 1), 'Unassigned')"""
    if topic:
        field_label = '%s'
        parameters.insert(0, topic['requested_query'])
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"""
                WITH due AS (
                    SELECT p.*, i.primary_domain AS institution_domain,
                        {field_label} AS sample_field
                    FROM professors p JOIN institutions i ON i.id = p.institution_id
                    WHERE {eligibility}
                ) SELECT * FROM due
                  ORDER BY {ordering} LIMIT %s
            """, parameters)
            rows = list(cursor.fetchall())
            for row in rows:
                if topic:
                    cursor.execute("""SELECT paper.*, paper.id AS paper_id, pp.raw_affiliation_text,
                            pp.affiliation_status, pp.affiliation_version, pp.affiliation_text,
                            pp.affiliation_institution, pp.affiliation_email, pp.affiliation_source_url,
                            pp.affiliation_checked_at, e.matched_query
                        FROM radar_topic_professor_papers e
                        JOIN papers paper ON paper.id = e.paper_id
                        JOIN professor_papers pp ON pp.professor_id = e.professor_id AND pp.paper_id = e.paper_id
                        WHERE e.professor_id = %s AND e.radar_topic_id = %s AND e.is_current_match
                        ORDER BY e.relevance_score DESC, paper.publication_year DESC NULLS LAST, paper.id DESC
                        LIMIT 3""", (row['id'], topic['id']))
                    row['recent_papers'] = list(cursor.fetchall())
                    continue
                cursor.execute("""SELECT paper.*, paper.id AS paper_id, pp.raw_affiliation_text,
                        pp.affiliation_status, pp.affiliation_version, pp.affiliation_text,
                        pp.affiliation_institution, pp.affiliation_email, pp.affiliation_source_url,
                        pp.affiliation_checked_at,
                        (SELECT e.matched_query FROM radar_topic_professor_papers e
                         WHERE e.professor_id = pp.professor_id AND e.paper_id = paper.id AND e.is_current_match
                         ORDER BY e.relevance_score DESC LIMIT 1) AS matched_query
                    FROM professor_papers pp JOIN papers paper ON paper.id = pp.paper_id
                    WHERE pp.professor_id = %s ORDER BY paper.publication_year DESC NULLS LAST, paper.id DESC LIMIT 3""", (row['id'],))
                row['recent_papers'] = list(cursor.fetchall())
            return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=30)
    parser.add_argument('--max-searches-per-candidate', type=int,
                        default=setting_int('FACULTY_IDENTITY_PASS_QUERIES', 10, 1, 10),
                        help='Logical queries per candidate, 1–10; defaults to .env')
    parser.add_argument('--random-seed', type=int, help='Reproducible randomized selection of eligible candidates')
    parser.add_argument('--field', default='', help='Sample only an existing research index and its exact supporting papers; no OpenAlex calls')
    parser.add_argument('--exclude-report', type=Path, help='Previous results.jsonl whose candidates should be excluded')
    parser.add_argument('--exclude-all-previous', action='store_true', help='Exclude every candidate in existing diagnostic reports')
    parser.add_argument('--include-recently-checked', action='store_true', help='Diagnostic only: ignore recheck dates; never changes saved decisions or schedules')
    parser.add_argument('--replay-report', type=Path, help='Repeat exact candidate IDs from a previous results.jsonl; read-only')
    args = parser.parse_args()
    args.max_searches_per_candidate = max(1, min(10, args.max_searches_per_candidate))
    excluded_ids = []
    exclusions = [args.exclude_report] if args.exclude_report else []
    if args.exclude_all_previous:
        exclusions += list((Path(__file__).resolve().parents[1] / 'reports').glob('verification-sample-*/results.jsonl'))
    excluded_ids = list({int(json.loads(line)['professor_id']) for report in exclusions
                        for line in report.read_text(encoding='utf-8').splitlines() if line.strip()})
    replay_ids = None
    if args.replay_report:
        replay_ids = list(dict.fromkeys(int(json.loads(line)['professor_id'])
            for line in args.replay_report.read_text(encoding='utf-8').splitlines() if line.strip()))
    try:
        selected = candidates(max(1, min(30, args.limit)), args.random_seed, excluded_ids, replay_ids, args.field, args.include_recently_checked)
    except ValueError as error:
        parser.error(str(error))
    now = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = Path(__file__).resolve().parents[1] / 'reports' / f'verification-sample-{now}'
    output.mkdir(parents=True, exist_ok=False)
    results, summary = [], Counter()
    providers = _provider_names()
    before = {
        provider: provider_capacity(provider).get('requests_total', 0)
        for provider in providers
    }
    print(json.dumps({'event': 'sample_started', 'candidates': len(selected), 'report': str(output),
                      'max_queries_per_candidate': args.max_searches_per_candidate,
                      'providers': providers, 'random_seed': args.random_seed,
                      'requested_field': args.field or None,
                      'include_recently_checked': args.include_recently_checked,
                      'excluded_previous_candidates': len(excluded_ids)}), flush=True)
    # Deliberately do not override SEARCH_PROVIDERS or SEARCH_PROVIDER_STRATEGY:
    # this diagnostic must exercise the same provider pool configured in .env.
    env = {'ORCID_ENABLED': 'false', 'PAPER_AFFILIATION_OPENALEX_LOOKUP_ENABLED': 'false',
           'FACULTY_VERIFY_OPENALEX_SUPPORT_ENABLED': 'false',
           'FACULTY_IDENTITY_PASS_QUERIES': str(args.max_searches_per_candidate)}
    with patch.dict(os.environ, env), patch.object(verifier, 'assess_identity_with_gemini', return_value=None), \
         patch('ingestion.paper_affiliations._save_result'):
        for index, candidate in enumerate(selected, 1):
            queries, pages = [], []
            original_inspect = verifier.inspect_faculty_result
            def inspect(c, result):
                decision = original_inspect(c, result)
                pages.append({'url': result.get('href'), 'status': decision.get('status'),
                              'institution': decision.get('institution_name'), 'title': decision.get('title'),
                              'method': decision.get('method'), 'evidence': decision.get('evidence_text')})
                return decision
            def bounded_search(query, max_results=5):
                if len(queries) >= args.max_searches_per_candidate:
                    raise SearchUnavailable('Sample query budget reached; full verification needs more searches', 60)
                capacities = [provider_capacity(provider) for provider in providers]
                next_slot = min(capacities, key=lambda capacity: capacity['retry_after_seconds'])
                delay = next_slot['retry_after_seconds']
                if delay > 70:
                    raise SearchUnavailable(f"Sample sources waiting: {next_slot.get('reason')}", delay)
                if delay:
                    print(json.dumps({'event': 'sample_wait', 'candidate': candidate['name'], 'seconds': delay}), flush=True)
                deadline = time.monotonic() + delay + 0.2
                while time.monotonic() < deadline:
                    verifier.audit_log.remaining_seconds()
                    time.sleep(min(5, max(0, deadline - time.monotonic())))
                queries.append({'query': query, 'started_at': datetime.now(timezone.utc).isoformat()})
                request_before = {
                    provider: provider_capacity(provider).get('requests_total', 0)
                    for provider in providers
                }
                try:
                    answer = search_web(query, max_results)
                except Exception as error:
                    queries[-1]['error_type'] = type(error).__name__
                    raise
                finally:
                    request_after = {
                        provider: provider_capacity(provider).get('requests_total', 0)
                        for provider in providers
                    }
                    # Counters include attempted requests, not only successful ones.
                    queries[-1]['outbound_attempts'] = {
                        provider: request_after[provider] - request_before[provider]
                        for provider in providers
                    }
                queries[-1]['results'] = answer
                return answer
            started = time.monotonic()
            print(json.dumps({'event': 'sample_candidate', 'index': index, 'name': candidate['name'], 'field': candidate['sample_field']}), flush=True)
            try:
                with patch.object(verifier, 'search_web', side_effect=bounded_search), \
                     patch.object(verifier, 'inspect_faculty_result', side_effect=inspect):
                    decision = verifier.verify_faculty_candidate(candidate)
                status = decision.get('status', 'UNVERIFIED')
                reason = decision.get('reason') or decision.get('evidence_text') or 'No decisive faculty-role evidence from this bounded pass.'
            except SearchUnavailable as error:
                status = 'SAMPLE_LIMIT' if 'Sample query budget' in str(error) else 'SOURCE_WAIT'
                reason, decision = str(error), {'search_audit': getattr(error, 'search_audit', {})}
            except Exception as error:
                status, reason, decision = 'ERROR', f'{type(error).__name__}: {error}', {}
            # Raw results were printed while checking. Persist only the same
            # selected sources retained by the verifier, not every noisy link.
            saved_urls = {r['url'] for r in (decision.get('search_audit') or {}).get('results', [])}
            for query in queries:
                raw = query.pop('results', [])
                query['returned_count'] = len(raw)
                query['results'] = [r for r in raw if r.get('href') in saved_urls]
            row = {'index': index, 'professor_id': candidate['id'], 'name': candidate['name'],
                   'field': candidate['sample_field'], 'imported_university': candidate['institution_name'],
                   'status': status, 'reason': reason, 'source': decision.get('source_url'),
                   'observed_role': decision.get('title'), 'observed_university': decision.get('institution_name'),
                   'method': decision.get('method'), 'failure_code': decision.get('failure_code'),
                   'search_audit': decision.get('search_audit') or {},
                   'papers': [{'title': p['title'], 'doi': p.get('doi'), 'pdf_url': p.get('pdf_url'), 'raw_affiliation': p.get('raw_affiliation_text')} for p in candidate['recent_papers']],
                   'queries': queries, 'pages': pages, 'seconds': round(time.monotonic() - started, 2)}
            row['useful_sources'] = useful_sources(decision)
            results.append(row)
            summary[status] += 1
            with (output / 'results.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(row, default=str) + '\n')
            print(json.dumps({'event': 'sample_result', 'index': index, 'name': candidate['name'], 'status': status, 'queries': len(queries)}), flush=True)
    attempts = {
        provider: provider_capacity(provider).get('requests_total', before[provider]) - before[provider]
        for provider in providers
    }
    final = {'candidates': len(results), 'outcomes': dict(summary),
             'requested_field': args.field or None,
             'include_recently_checked': args.include_recently_checked,
             'random_seed': args.random_seed, 'excluded_previous_candidates': len(excluded_ids),
             'max_queries_per_candidate': args.max_searches_per_candidate,
             'outbound_search_attempts': attempts,
             'identities_saved': 0, 'report': str(output), 'completed_at': datetime.now(timezone.utc).isoformat()}
    (output / 'summary.json').write_text(json.dumps(final, indent=2), encoding='utf-8')
    attempts_label = ', '.join(f'{provider}: {count}' for provider, count in attempts.items())
    (output / 'REPORT.md').write_text(
        render_markdown_report(results, attempts_label, args.max_searches_per_candidate),
        encoding='utf-8',
    )
    print(json.dumps({'event': 'sample_complete', **final}), flush=True)


if __name__ == '__main__':
    main()
