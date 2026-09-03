"""Bounded, per-candidate diagnostics; no global cross-user mutable evidence."""
from contextvars import ContextVar
from datetime import datetime, timezone
import ipaddress
import json
import time
from urllib.parse import urlparse
from settings import setting_bool

CURRENT = ContextVar('faculty_verification_audit', default=None)


class IdentityPassLimit(RuntimeError):
    pass


def remaining_seconds():
    audit = CURRENT.get()
    deadline = (audit or {}).get('_search_deadline')
    if deadline is None:
        # Page analysis and evidence storage have no aggregate clock. Their
        # individual network calls retain their own socket timeouts.
        return 86_400.0
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise IdentityPassLimit('This search query reached its 90-second time limit.')
    return seconds


def begin_search(seconds):
    audit = CURRENT.get()
    if audit is not None:
        audit['_search_deadline'] = time.monotonic() + max(1, int(seconds))


def end_search():
    audit = CURRENT.get()
    if audit is not None:
        audit.pop('_search_deadline', None)


def emit(event, **details):
    audit = CURRENT.get()
    if audit is None or not setting_bool('FACULTY_IDENTITY_VERBOSE_LOG', True):
        return
    # JSON encoding prevents untrusted snippets injecting fake terminal lines.
    print(json.dumps({'timestamp': datetime.now(timezone.utc).isoformat(),
                      'event': event, 'candidate': audit.get('candidate_name'),
                      'field': audit.get('field'), **details}, ensure_ascii=True, default=str), flush=True)


def finish(audit, decision):
    all_results = audit['results']
    chosen = [row for row in all_results if row.get('source_kind') in
              {'university', 'personal', 'lab', 'linkedin', 'cv'}]
    audit['links_collected'] = len(all_results)
    audit['results'] = chosen or all_results[:10]
    audit['retention'] = 'identity_sources' if chosen else 'first_ten_no_identity_source'
    audit['outcome'] = decision.get('status', 'UNVERIFIED')
    if audit['outcome'] in {'VERIFIED', 'NOT_FACULTY', 'OUT_OF_SCOPE', 'CONFLICT'}:
        audit['stopping_reason'] = {
            'VERIFIED': 'Stopped early: faculty identity established',
            'NOT_FACULTY': (
                'Completed targeted searches: possibly not faculty; staff confirmation recommended'
                if decision.get('review_recommended')
                else 'Stopped early: attributable non-faculty role established'
            ),
            'OUT_OF_SCOPE': 'Stopped early: faculty role is outside the current US-only scope',
            'CONFLICT': 'Stopped early: identity conflict requires review',
        }[audit['outcome']]
    audit['failure_code'] = decision.get('failure_code')
    audit['source_url'] = decision.get('source_url')
    for key in ('scope_status', 'scope_reason', 'country_code', 'institution_name',
                'role_category', 'observed_employer', 'currentness', 'source_type',
                'lookup_status'):
        if decision.get(key):
            audit[key] = decision[key]
    audit['reason'] = decision.get('reason') or decision.get('evidence_text') or 'No decisive role evidence'
    audit['searches_sent'] = sum(q.get('kind', 'search') == 'search' for q in audit['queries'])
    emit('identity_decision', status=audit['outcome'], reason=audit['reason'],
         failure_code=audit['failure_code'], links_collected=audit['links_collected'],
         links_saved=len(audit['results']), role=decision.get('title'),
         university=decision.get('institution_name'), source=decision.get('source_url'),
         stopping_reason=audit.get('stopping_reason'), scope_status=decision.get('scope_status'))
    # HTML and full query results live only within this check, not in PostgreSQL.
    for key in list(audit):
        if key.startswith('_'):
            audit.pop(key)
    return audit


def safe_source_link(url):
    """Human-clickable source link, not permission for the crawler to fetch it."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower().rstrip('.')
        if parsed.scheme not in {'http', 'https'} or not host or parsed.username or parsed.password:
            return False
        if host in {'localhost', 'localhost.localdomain'} or host.endswith('.local'):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return '.' in host
    except ValueError:
        return False


def record_results(query, results):
    from ingestion.identity_sources import canonical_source_url
    audit = CURRENT.get()
    if audit is None:
        return
    kind = 'link' if query.startswith(('Link followed from ', 'Previously saved profile URL')) else 'search'
    audit['queries'].append({'query': query[:500], 'returned': len(results), 'kind': kind})
    emit('identity_search_complete', query=query[:500], returned=len(results))
    existing = {r['url'] for r in audit['results']}
    for result in results:
        url = canonical_source_url(result.get('href') or '')
        result['href'] = url
        if not url or url in existing or len(audit['results']) >= audit.get('_result_limit', 100):
            continue
        existing.add(url)
        audit['results'].append({'url': url[:2000], 'title': str(result.get('title') or '')[:400],
            'snippet': str(result.get('body') or '')[:1600], 'query': query[:500],
            'inspection': 'Not inspected in this pass', 'snippet_hint': '',
            'source_kind': result.get('source_kind'), 'discovered_from': result.get('discovered_from')})
        emit('identity_search_result', rank=len(audit['results']), query=query[:500],
             url=url[:2000], title=str(result.get('title') or '')[:400],
             snippet=str(result.get('body') or '')[:1600])


def record_page(url, decision):
    audit = CURRENT.get()
    if audit is None:
        return decision
    event = {'url': str(url)[:2000], 'status': decision.get('status', 'UNVERIFIED'),
        'reason': decision.get('reason') or decision.get('evidence_text') or decision.get('method') or 'No attributable current faculty role established',
        'role': decision.get('title'), 'institution': decision.get('institution_name'),
        'failure_code': decision.get('failure_code'),
        'role_category': decision.get('role_category'),
        'observed_employer': decision.get('observed_employer'),
        'currentness': decision.get('currentness'),
        'source_type': decision.get('source_type'),
        'lookup_status': decision.get('lookup_status'),
        'scope_status': decision.get('scope_status')}
    document = audit.get('_documents', {}).get(url, {})
    event.update({key: document[key] for key in (
        'http_status', 'final_url', 'content_type', 'response_bytes', 'title',
        'headings', 'text_excerpt', 'name_context', 'rendering_hint', 'encoding') if key in document})
    if len(audit['pages']) < 40:
        audit['pages'].append(event)
    for row in audit['results']:
        if row['url'] == url:
            row['inspection'] = str(event['reason'])[:1000]
            row['decision'] = event['status']
    emit('identity_page_decision', **event)
    return decision


def note_result(url, reason, snippet_hint=''):
    audit = CURRENT.get()
    if audit is not None:
        for row in audit['results']:
            if row['url'] == url:
                if reason:
                    row['inspection'] = reason
                if snippet_hint:
                    row['snippet_hint'] = snippet_hint
                emit('identity_result_screened', url=url, reason=reason, snippet_hint=snippet_hint)


def new_audit(candidate):
    fields = list(dict.fromkeys(p['matched_query'] for p in candidate.get('recent_papers') or [] if p.get('matched_query')))
    return {'checked_at': datetime.now(timezone.utc).isoformat(), 'version': 3,
        'candidate_name': candidate.get('name'),
        'field': ', '.join(fields) or candidate.get('sample_field') or candidate.get('research_domain'),
        'imported_university': candidate.get('institution_name'),
        'queries': [], 'results': [], 'pages': [], 'outcome': 'RUNNING'}


def record_retrieval(query, provider, results, outcome):
    """Keep a small diagnostic sample, not every discarded provider response."""
    audit = CURRENT.get()
    if audit is None:
        return
    event = {'query': query[:500], 'provider': provider, 'outcome': outcome,
             'returned_count': len(results), 'sample': [
                 {'url': str(r.get('href') or '')[:2000],
                  'title': str(r.get('title') or '')[:400],
                  'snippet': str(r.get('body') or '')[:600]} for r in results[:3]]}
    if len(audit.setdefault('retrieval_events', [])) < 30:
        audit['retrieval_events'].append(event)
    emit('identity_retrieval', **event)
