# Faculty verification diagnostic

Candidates: 30 · DuckDuckGo outbound attempts: 25

One query per candidate by default. No identity decisions were published or saved.

## What this test actually found

- 30 candidates selected across research areas from the active Ubuntu database.
- 25 new DuckDuckGo requests; 5 queries reused results from the initial diagnostic.
- 26 first queries returned no results. Four returned search links.
- No candidate was verified within the one-query allowance.
- All 30 reached `SAMPLE_LIMIT`: the verifier wanted another query, which this
  diagnostic deliberately did not send. This is not a `NOT_FACULTY` decision.
- No OpenAlex, Gemini, Tavily or Brave API credits were used by this diagnostic.

This is a first-query retrieval diagnostic, **not a complete multi-query
verification run or a measured full-verifier success rate**. Empty search results
do not prove that a professor or their official page does not exist. These results
do not distinguish restrictive queries from search-index gaps or silent provider
failures. A follow-up evaluation must allow the normal additional queries and
record page-fetch outcomes before claiming the verification problem is solved.

## Applied changes

The active project is `/home/mel/search_prof`, not the Downloads/MVP copy.

- Read author-specific OpenAlex affiliation metadata first.
- Resolve missing PDF links from DOI landing pages; inspect at most three recent
  accessible papers. Fixed a shared URL filter that previously rejected `.pdf`.
- Link author blocks/numbered affiliations conservatively; do not take a
  coauthor's university merely because it appears in the same document.
- Search the person's name with the supported university; use supporting field
  evidence for disambiguation. Inspect actual pages and current role statements.
- Different observed university: `CONFLICT`, hidden and not automatically retried.
  Staff can resolve it or explicitly request another check. Real moves also need review.
- Keep incomplete evidence unresolved. A paper affiliation is not proof of a
  current faculty appointment.
- Check Tavily-reported usage, track Brave response limit windows, retain local
  caps, and fall back to the next configured provider, with DuckDuckGo last.
- Keep completed identity decisions for at least 30 days. Search failures are
  not completed identity checks. Hiring/grant refresh schedules are separate.

205 tests, including isolated PostgreSQL checks, passed. Database and all seven
Streamlit page smoke tests passed. API quota responses were tested with mocks;
live paid-provider testing requires configured keys. Provider dashboard spending
caps are still necessary because usage reports can lag or change in other apps.

Database backup: `/home/mel/search_prof/backups/scholarradar-20260830T180400Z.dump`.
The full worker was not started. See `SEARCH_PROVIDERS.md` for configuration.

## Individual cases

| Candidate | Field | Outcome |
|---|---|---|
| Timothy Oladunni | Artificial intelligence | SAMPLE_LIMIT |
| Tianyi Zhang | Natural language processing | SAMPLE_LIMIT |
| Kenneth M. Merz | Machine learning | SAMPLE_LIMIT |
| Boran Ma | Environmental engineering | SAMPLE_LIMIT |
| Patrick J Egan | Political science | SAMPLE_LIMIT |
| Saiteja Malisetty | Cybersecurity | SAMPLE_LIMIT |
| Zengyi Huang | Computer science | SAMPLE_LIMIT |
| Nachiappan Nagappan | Software engineering | SAMPLE_LIMIT |
| Jinglun Feng | Robotics | SAMPLE_LIMIT |
| Tohid Kargar Tasooji | Distributed systems | SAMPLE_LIMIT |
| Michael Variny | Aerospace engineering | SAMPLE_LIMIT |
| Pengkun Liu | Civil engineering | SAMPLE_LIMIT |
| Ying Ding | Biomedical engineering | SAMPLE_LIMIT |
| Neha Parikh | Biomed | SAMPLE_LIMIT |
| Jing Cao | Asian studies | SAMPLE_LIMIT |
| Nyzaireyus Harrison | AI security | SAMPLE_LIMIT |
| Mahsa Asadi Anar | Human-computer interaction | SAMPLE_LIMIT |
| Mohammad Hosseini | Data science | SAMPLE_LIMIT |
| Yunjie Tian | Computer vision | SAMPLE_LIMIT |
| René Burress | Information science | SAMPLE_LIMIT |
| Marcelo Godoy Simões | Electrical engineering | SAMPLE_LIMIT |
| Haodong Hu | Mechanical engineering | SAMPLE_LIMIT |
| Vicente Talanquer | Chemical engineering | SAMPLE_LIMIT |
| Mohammed W. Abdulrahman | Nuclear engineering | SAMPLE_LIMIT |
| Mehran Bahrami | Materials engineering | SAMPLE_LIMIT |
| Modupe Arowolo | Industrial engineering | SAMPLE_LIMIT |
| Jin Yang | Biology | SAMPLE_LIMIT |
| Humphrey Shi | Natural language processing | SAMPLE_LIMIT |
| Andrew R. Flores | Political science | SAMPLE_LIMIT |
| Shuo Wang | Machine learning | SAMPLE_LIMIT |
