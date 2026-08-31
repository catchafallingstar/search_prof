# Faculty role attribution (version 9)

## What was wrong

The old rule searched a broad window around the candidate's name for any faculty
title. It accepted the positive title before handling student/postdoc evidence.
On Saiteja Malisetty's profile, the candidate's Ph.D. status and his mentor's
Professor title appear together. The mentor's title was incorrectly assigned to
the candidate. A university domain and a matching page title did not prevent it.

## Corrected decisions

| Evidence actually found | Decision |
|---|---|
| Candidate has a current faculty role; profile university matches the paper/imported university | VERIFIED |
| Candidate has an explicit student or postdoc role at the matching university | NOT_FACULTY, with the observed role recorded as evidence |
| An attributable profile role points to a different university | CONFLICT; staff review, no automatic retry |
| Profile does not state an attributable role, cannot be accessed, or has contradictory roles | UNVERIFIED; not automatically classified as a student |

University comparisons use normalized names, not exact string equality: for
example, “University of Nebraska Omaha” and “University of Nebraska at Omaha”
refer to the same institution. An institution's domain is used to identify the
host university, but guest-speaker pages do not establish employment there.

The rule now checks who the role describes. It excludes titles belonging to an
advisor, collaborator, or neighboring directory entry, and distinguishes prior
student roles from current faculty roles. A professor who supervises PhD students
does not become a student. Explicit negative results stop the search early rather
than buying another query. Missing evidence is not a negative identity decision.

Personal/lab pages still require supporting paper evidence and a university
attached to the role. AI-assisted extraction cannot override explicit attributed
student evidence. This is a conservative heuristic, not proof that every future
identity will be classified correctly.

## Existing database records

New decisions use faculty-verification version 9. Version 8 public records were
not bulk deleted, hidden, or rewritten. Normal recency/caching rules remain in
place; this patch is not a retrospective audit of every stored identity. Live
diagnostics do not save faculty decisions. Conflicts remain review-only.

## Random diagnostics

The diagnostic script now accepts a seed for reproducible random selection and
an exclusion report, so another test does not simply repeat the previous people.

```bash
cd ~/search_prof
source .venv/bin/activate
python -m scripts.sample_faculty_verification \
  --limit 30 --random-seed 20260830 \
  --exclude-report reports/verification-sample-20260830T200538Z/results.jsonl \
  --max-searches-per-candidate 2
```

The sample is drawn from due US candidates linked to current research topics,
not all people worldwide. `SAMPLE_LIMIT` means the bounded test needed more
queries; it does not prove the person is not faculty. `SOURCE_WAIT` means a
provider was unavailable. The script exits after processing the selected cases.

## Timeout handling

A single SearchAPI read timeout exposed a separate operational issue: transport
failures previously triggered a one-hour pause. Ordinary Requests timeouts and
connection errors now use `SEARCH_NETWORK_BACKOFF_SECONDS` (default 60 seconds).
HTTP rate limits, authentication failures, credit exhaustion, and local request
caps keep their existing longer safeguards. No quota counters are reset.

Source for the known student/advisor regression:
https://www.unomaha.edu/college-of-information-science-and-technology/phd-it/directory/saiteja-malisetty.php
