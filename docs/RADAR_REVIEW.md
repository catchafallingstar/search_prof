# ScholarRadar radar review

## The short conclusion

The radar was not completely unusable, but its first version was too willing to
connect weak clues. The observed `AI security` result demonstrates the problem:
OpenAlex returned a topic called `Innovative Educational Techniques`. One topic
keyword was `Artificial Intelligence`; a different keyword was `Border Security`.
The old matcher joined every keyword into one text value, found both query words,
and accepted the unrelated topic.

The corrected matcher requires the query concepts to occur together in one topic
name or one keyword phrase. For this query, no OpenAlex topic passes that test, so
the system safely keeps the user's exact query and uses a direct works search.
`Unknown` in the taxonomy field is therefore a safe fallback, not an error.

## Why the homepage showed zero results

The red Search button searches rows already stored in the `opportunities` table.
It intentionally does not run OpenAlex, NSF, homepage searches, and social searches
inside a visitor's web request. That would take too long, produce inconsistent
results, and let public traffic exhaust external services.

The earlier radar run created professors, papers, and grant evidence. Those are not
openings. It found no trustworthy hiring statement, so there was no active
opportunity for the homepage to display. A GPA filter makes this even narrower:
public radar evidence uses `not_stated`; it may not invent a professor's GPA policy.

Large search sites feel fast because they collect and moderate records in the
background, then their web search reads a prebuilt index. ScholarRadar should use
the same architecture:

```text
scheduled/admin radar -> candidate evidence -> moderation -> active opportunities
visitor Search button -------------------------------------> database query
```

## What each radar stage now means

### 1. `ingestion/taxonomy.py`: interpret the query

- Calls OpenAlex Topics.
- Rejects fuzzy topics that scatter query concepts across unrelated keywords.
- Treats `AI` and `artificial intelligence` as the same concept.
- Falls back to the exact text when no coherent topic exists.

It does not understand a person's intent as deeply as a human. `AI security` can
mean security *of* AI, or AI applied to food, national, cloud, or cyber security.
For controlled tests, use a precise phrase such as `adversarial machine learning`
or `security of artificial intelligence systems`.

### 2. `ingestion/fetch_prof.py`: discover researchers from papers

- Requests recent US-affiliated OpenAlex works.
- When taxonomy falls back, keeps only works whose title, one topic, or one keyword
  contains the query concepts close together.
- Selects the corresponding author, or otherwise the last author, as a probable PI.
- Saves institutions, professors, papers, and professor-paper links.

This stage discovers candidates; it does not prove that an author is a professor,
controls a lab, or is recruiting. Corresponding/last author is only a useful
heuristic. A production system should store all relevant authorships and add an
explicit faculty-identity enrichment step rather than treating paper position as
proof.

### 3. `ingestion/check_grants.py`: add NSF funding evidence

- Runs only when the taxonomy routes to NSF.
- Checks only professors from the current research-domain scan.
- Uses NSF's PI-name parameter and active-award filter.
- Requires both given and family name, and also checks institution similarity.
- De-duplicates grants by a stable hash.

A grant never means “this lab is hiring,” and it never supplies a GPA policy. It is
supporting evidence and a ranking clue only. NIH and other funders still need their
own connectors.

### 4. `ingestion/homepagefinder.py`: find a likely official page

- Searches for faculty/lab pages.
- Requires both the professor's given and family name in the search result.
- Rejects social profiles, document downloads, localhost, private IPs, and other
  unsafe or unsuitable sources.

Name matching reduces wrong-person results, but common names and stale search
snippets still require moderation.

### 5. `ingestion/socialradar.py`: look for public recruiting text

- Tries public Bluesky search, then ordinary web-search snippets.
- Requires both given and family name and an explicit recruiting phrase.
- Disables Bluesky for the remainder of a scan after HTTP 401, 403, or 429, then
  continues with the other sources.

The observed Bluesky 403 is a source-availability problem, not a database failure.
Social evidence remains optional. A same-name account is still possible, so these
results require review.

### 6. `ingestion/matchers.py`: classify the wording

- Looks for an action such as `recruiting`, `seeking`, or `accepting` near a role
  such as `PhD`, `postdoc`, or `research assistant`.
- Rejects negative wording such as `not accepting` or `position has been filled`.
- Extracts role and funding-language clues.
- Rejects clearly old dates when a search snippet starts with one.

This is deliberately precision-first. It will miss unusual wording rather than
publish a weak implication as a hiring fact.

### 7. `ingestion/parse_hiring_signals.py`: coordinate and save candidates

- Fetches pages while validating every redirect against private-network access.
- Preserves HTML block boundaries so unpunctuated recruiting cards can be found.
- Tries a homepage before public social/web snippets.
- Saves the original quote, URL, confidence, dates, and hash.
- Creates a `pending` public-signal opportunity, never an automatically active one.

An owner or moderator sees this candidate in `pages/3_Admin_review.py`. Only an
approval changes it to an active homepage result.

### 8. `scripts/run_radar.py`: command-line orchestrator

This script calls the stages in order. `--skip-web-signals` stops after researcher
and grant discovery, so it cannot produce a candidate opportunity. This is useful
for testing taxonomy and paper discovery, but it will not populate the public page.

## Fixed defects

1. **Unrelated taxonomy match:** keywords from different phrases were joined.
2. **Broad direct-work results:** fallback results were not checked for coherent
   query coverage.
3. **Weak NSF lookup:** the earlier request/matching could associate a grant with a
   same-family-name researcher. The current code uses NSF's PI parameter and checks
   full identity plus institution.
4. **Unscoped grant scans:** a research-area run could recheck every professor in
   the database. It is now scoped to that run's domain.
5. **Weak web identity:** homepage/social matches formerly relied too heavily on a
   family name. Both given and family names are now required.
6. **Bluesky log flood:** every professor produced a 403. One source failure now
   disables that source for the current scan.
7. **Unsafe redirect gap:** the destination of each HTTP redirect is now validated
   before it is fetched.
8. **Poor HTML segmentation:** flattening a page could hide recruiting text without
   punctuation. Paragraph/list/heading boundaries are now preserved.
9. **Automatic publication risk:** machine-discovered evidence now enters the
   moderation queue as `pending`.
10. **Confusing empty search:** the homepage now explains whether the database has
    no active records or only the selected filters have no match.

## Important limits that remain

- The taxonomy/direct-work filter is lexical, not a semantic intent model.
- The scan covers a limited number of recent papers, not the entire professor
  population.
- OpenAlex authorship position is not proof of faculty status.
- The current professor record has one `research_domain` string; a future schema
  should use a many-to-many professor-topic table.
- Static faculty pages often omit a publication date. Moderators must judge whether
  text is still current.
- Public search services may block, throttle, or change behavior.
- There is no scheduler yet. Runs are manual until a cron/job service is added.
- There is no semantic deduplication when the same announcement is rewritten.
- Funding scores need a future decay/recalculation policy as awards expire.
- Only active, moderator-approved opportunities appear to visitors.

These are reasons to call this an MVP radar, not reasons to discard it. The safe
product strategy is evidence collection plus human moderation, not fully automatic
claims about who is hiring.

## Updating the live WSL copy

The maintained source is under:

```text
/mnt/c/Users/2023m/Documents/Codex/2026-08-02/so/outputs/scholarradar_mvp
```

The app you execute is:

```text
/home/mel/search_prof
```

`C:\Users\2023m\Downloads\search_prof` is another copy. Editing or uploading it
does not change the WSL app. Confirm the active import at any time:

```bash
cd ~/search_prof
python -c "import ingestion.taxonomy as t; print(t.__file__)"
```

Use the generated overlay archive to update code without replacing `.env`:

```bash
cd ~/search_prof
unzip -o /mnt/c/Users/2023m/Documents/Codex/2026-08-02/so/outputs/scholarradar_radar_fix.zip
python -m unittest discover -s tests -v
python -m scripts.smoke_test_streamlit
```

## Removing the already imported bad test data

Back up first:

```bash
cd ~/search_prof
set -a
source .env
set +a
docker compose exec -T postgres pg_dump \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB" > before-radar-cleanup.sql
```

Inspect the exact records before deleting anything:

```bash
docker compose exec -T postgres psql \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "SELECT id, name, institution_name, research_domain FROM professors WHERE research_domain = 'Innovative Educational Techniques' ORDER BY id;"
```

If that list contains only the incorrect test import, remove those radar-created
records while preserving any professor linked to a submitted profile:

```bash
docker compose exec -T postgres psql \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<'SQL'
BEGIN;
CREATE TEMP TABLE bad_professor_ids AS
SELECT p.id
FROM professors p
WHERE p.research_domain = 'Innovative Educational Techniques'
  AND NOT EXISTS (
    SELECT 1 FROM professor_profiles pp WHERE pp.professor_id = p.id
  );

DELETE FROM opportunities
WHERE source_kind = 'public_signal'
  AND professor_id IN (SELECT id FROM bad_professor_ids);

DELETE FROM professors
WHERE id IN (SELECT id FROM bad_professor_ids);

DELETE FROM papers
WHERE NOT EXISTS (
  SELECT 1 FROM professor_papers pp WHERE pp.paper_id = papers.id
);
COMMIT;
SQL
```

Do not delete the PostgreSQL volume merely to repair these ten records.

## A controlled retest

First verify the regression itself:

```bash
python -c "from ingestion.taxonomy import normalize_taxonomy; x=normalize_taxonomy('AI security'); print(x['topic_name'], '/', x['field_name'], '/', x['topic_id'])"
```

Expected: the exact query with `Unknown` and `None`, plus a message that no relevant
OpenAlex topic was accepted. Then test an unambiguous subject without web sources:

```bash
python -m scripts.run_radar "adversarial machine learning" \
  --max-papers 20 --skip-web-signals
```

Finally, run the web evidence stage with a small batch, inspect pending evidence,
and approve only records whose page clearly identifies the researcher and current
recruiting statement:

```bash
python -m scripts.run_radar "adversarial machine learning" --max-papers 10
python -m scripts.inspect_signals
streamlit run app.py
```

Open Staff review in the app. A candidate does not appear publicly until you approve
it. The GPA filter should normally remain `All policies` for radar-discovered text.
