# ScholarRadar university-first pipeline

## Source of truth

New professors enter ScholarRadar only from an approved faculty roster on a
registered university domain. OpenAlex, DDGS snippets, awards pages, news
articles, and individual profiles cannot create professor records.

## Pipeline

1. **Institution registry**
   - Preserve the U.S. College Scorecard institution list.
   - Store canonical institution names, aliases, official domains, and campus
     identities separately.

2. **Discover official faculty pages**
   - Inspect the university sitemap and navigation first.
   - Use one DDGS query only when official navigation does not locate a roster.
   - Keep discovery jobs queued while the shared search slot is unavailable.

3. **Faculty-page validation**
   - Require the registered university domain or its subdomain.
   - Reject awards, news, alumni, archive, event, and research-project URLs.
   - Require repeated person-profile links with a faculty title attributable to
     each person's local card, list item, or table row.
   - Reject general employee-resource portals and individual profile pages.

4. **Import the roster**
   - Create or refresh canonical professor records only from approved rosters.
   - Keep faculty, adjunct, visiting, research, and emeritus appointment types
     distinct.
   - Mark a missing roster member inactive only after two successful checks.

5. **Resolve academic identity**
   - Match each roster professor to exactly one OpenAlex author using exact
     normalized name plus compatible institution, or a matching ORCID.
   - Enforce one OpenAlex identity per canonical professor.
   - Send zero or multiple compatible authors to staff review; attach no papers.

6. **Import publications**
   - Import papers only for uniquely matched authors.
   - Store paper authorship and affiliation-at-publication separately from the
     current university appointment.

7. **Build research-area indexes**
   - Match requested topics against each paper title and abstract.
   - A professor appears for a topic only with paper-specific evidence.

8. **Scheduled enrichment**
   - Refresh known hiring pages daily when an explicit opening is active, less
     often for weaker or absent signals.
   - Store exact hiring text and checked date.
   - Store lab GPA and university graduate-program GPA as separate facts.
   - Check grants without treating a name-only result as evidence.

9. **Refresh and review**
   - Refresh saved rosters monthly without search.
   - Use DDGS only to recover missing directories or moved sources.
   - Show failed jobs, questionable faculty pages, ambiguous OpenAlex matches,
     and unavailable hiring pages on the staff dashboard.

## Local operation

```bash
cd ~/search_prof
make db-up
make schema
make initialize-universities
make worker
```

`make start` runs PostgreSQL, Streamlit, and the worker together. Do not also
start a second standalone worker unless parallel non-search processing is
intended.

The destructive clean-start command is:

```bash
make backup
make reset-university-data
make schema
make initialize-universities
```

It preserves users, administrators, the institution registry, aliases/domains,
College Scorecard data, and backups. It clears derived faculty, directory,
paper, topic, enrichment, and queue data.
