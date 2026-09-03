-- ScholarRadar PostgreSQL schema
-- Apply this file explicitly. The web app does not mutate its own schema.

CREATE TABLE IF NOT EXISTS institutions (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    country_code CHAR(2),
    primary_domain TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT institutions_name_unique UNIQUE (name)
);

ALTER TABLE institutions ADD COLUMN IF NOT EXISTS organization_type TEXT NOT NULL DEFAULT 'UNKNOWN';
ALTER TABLE institutions ADD COLUMN IF NOT EXISTS organization_type_method TEXT;
ALTER TABLE institutions ADD COLUMN IF NOT EXISTS organization_type_checked_at TIMESTAMPTZ;
ALTER TABLE institutions ADD COLUMN IF NOT EXISTS scorecard_unit_id BIGINT;
ALTER TABLE institutions DROP CONSTRAINT IF EXISTS institutions_organization_type_check;
ALTER TABLE institutions ADD CONSTRAINT institutions_organization_type_check CHECK (
    organization_type IN (
        'HIGHER_EDUCATION', 'K12_SCHOOL', 'COMPANY', 'GOVERNMENT',
        'NATIONAL_LAB', 'RESEARCH_INSTITUTE', 'UNKNOWN'
    )
);

-- A local copy of the US Department of Education College Scorecard directory.
-- It is intentionally separate from institutions: the source directory can be
-- replaced atomically without changing professor records.
CREATE TABLE IF NOT EXISTS college_scorecard_institutions (
    unit_id BIGINT PRIMARY KEY,
    ope_id TEXT,
    institution_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    city TEXT,
    state_code TEXT,
    postal_code TEXT,
    homepage_url TEXT,
    primary_domain TEXT,
    is_main_campus BOOLEAN,
    is_currently_operating BOOLEAN,
    source_file TEXT,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS college_scorecard_institutions_name_idx
    ON college_scorecard_institutions (normalized_name);
CREATE INDEX IF NOT EXISTS college_scorecard_institutions_domain_idx
    ON college_scorecard_institutions (primary_domain)
    WHERE primary_domain IS NOT NULL;

CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    oidc_subject TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL,
    display_name TEXT NOT NULL,
    account_role TEXT NOT NULL DEFAULT 'applicant'
        CHECK (account_role IN ('applicant', 'professor', 'institution_admin')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT users_email_unique UNIQUE (email)
);

-- Site administration is intentionally separate from the user's academic role.
-- A professor can be a normal user, and a moderator does not need to be a professor.
CREATE TABLE IF NOT EXISTS site_admins (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    admin_role TEXT NOT NULL CHECK (admin_role IN ('owner', 'moderator')),
    granted_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ
);

-- There can be only one active owner. That owner can add or revoke moderators.
CREATE UNIQUE INDEX IF NOT EXISTS site_admins_one_active_owner_idx
    ON site_admins (admin_role)
    WHERE admin_role = 'owner' AND revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS admin_audit_log (
    id BIGSERIAL PRIMARY KEY,
    actor_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id BIGINT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS professors (
    id BIGSERIAL PRIMARY KEY,
    openalex_id TEXT,
    name TEXT NOT NULL,
    institution_id BIGINT REFERENCES institutions(id) ON DELETE SET NULL,
    institution_name TEXT NOT NULL,
    homepage_url TEXT,
    research_domain TEXT,
    career_stage TEXT NOT NULL DEFAULT 'UNKNOWN'
        CHECK (career_stage IN ('NEW_AP', 'ESTABLISHED_PI', 'UNKNOWN')),
    faculty_status TEXT NOT NULL DEFAULT 'UNVERIFIED'
        CHECK (faculty_status IN ('UNVERIFIED', 'VERIFIED', 'NOT_FACULTY', 'OUT_OF_SCOPE', 'CONFLICT', 'MANUAL_REVIEW')),
    faculty_title TEXT,
    faculty_source_url TEXT,
    faculty_verification_method TEXT,
    faculty_verification_version INTEGER NOT NULL DEFAULT 0,
    faculty_confidence NUMERIC(4, 3) NOT NULL DEFAULT 0
        CHECK (faculty_confidence BETWEEN 0 AND 1),
    faculty_checked_at TIMESTAMPTZ,
    faculty_verified_at TIMESTAMPTZ,
    next_identity_check_at TIMESTAMPTZ,
    public_hiring_checked_at TIMESTAMPTZ,
    public_hiring_check_status TEXT NOT NULL DEFAULT 'NOT_CHECKED'
        CHECK (public_hiring_check_status IN ('NOT_CHECKED', 'PRESENT', 'NOT_FOUND', 'SOURCE_UNAVAILABLE')),
    public_hiring_failure_count INTEGER NOT NULL DEFAULT 0,
    public_hiring_next_check_at TIMESTAMPTZ,
    grant_checked_at TIMESTAMPTZ,
    lab_gpa_policy TEXT NOT NULL DEFAULT 'not_stated'
        CHECK (lab_gpa_policy IN ('not_stated', 'no_lab_cutoff', 'minimum', 'holistic_review', 'exceptions_considered')),
    lab_gpa_evidence_text TEXT,
    lab_gpa_source_url TEXT,
    lab_gpa_minimum NUMERIC(3, 2),
    program_gpa_minimum NUMERIC(3, 2),
    program_gpa_source_url TEXT,
    gpa_last_checked_at TIMESTAMPTZ,
    previous_institutions TEXT[] NOT NULL DEFAULT '{}',
    official_institution_domain TEXT,
    appointment_year INTEGER CHECK (appointment_year IS NULL OR appointment_year BETWEEN 1900 AND 2200),
    appointment_start_date DATE,
    appointment_date_precision TEXT CHECK (appointment_date_precision IS NULL OR appointment_date_precision IN ('YEAR', 'MONTH', 'DAY')),
    graduate_faculty_status TEXT NOT NULL DEFAULT 'UNKNOWN'
        CHECK (graduate_faculty_status IN ('UNKNOWN', 'VERIFIED', 'NOT_LISTED')),
    radar_score INTEGER NOT NULL DEFAULT 0,
    score_breakdown TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT professors_openalex_unique UNIQUE (openalex_id),
    CONSTRAINT professors_name_institution_unique UNIQUE (name, institution_name)
);

ALTER TABLE professors ADD COLUMN IF NOT EXISTS faculty_status TEXT NOT NULL DEFAULT 'UNVERIFIED';
ALTER TABLE professors ADD COLUMN IF NOT EXISTS faculty_title TEXT;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS faculty_source_url TEXT;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS faculty_verification_method TEXT;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS faculty_verification_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS faculty_confidence NUMERIC(4, 3) NOT NULL DEFAULT 0;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS faculty_checked_at TIMESTAMPTZ;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS faculty_verified_at TIMESTAMPTZ;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS next_identity_check_at TIMESTAMPTZ;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS orcid_id TEXT;
-- Retry scheduling is not an identity decision or an identity freshness date.
ALTER TABLE professors ADD COLUMN IF NOT EXISTS identity_retry_at TIMESTAMPTZ;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS identity_retry_reason TEXT;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS public_hiring_checked_at TIMESTAMPTZ;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS public_hiring_check_status TEXT NOT NULL DEFAULT 'NOT_CHECKED';
ALTER TABLE professors ADD COLUMN IF NOT EXISTS public_hiring_failure_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS public_hiring_next_check_at TIMESTAMPTZ;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS grant_checked_at TIMESTAMPTZ;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS lab_gpa_policy TEXT NOT NULL DEFAULT 'not_stated';
ALTER TABLE professors ADD COLUMN IF NOT EXISTS lab_gpa_evidence_text TEXT;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS lab_gpa_source_url TEXT;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS lab_gpa_minimum NUMERIC(3, 2);
ALTER TABLE professors ADD COLUMN IF NOT EXISTS program_gpa_minimum NUMERIC(3, 2);
ALTER TABLE professors ADD COLUMN IF NOT EXISTS program_gpa_source_url TEXT;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS gpa_last_checked_at TIMESTAMPTZ;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS previous_institutions TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE professors ADD COLUMN IF NOT EXISTS official_institution_domain TEXT;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS appointment_year INTEGER;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS appointment_start_date DATE;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS appointment_date_precision TEXT;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS graduate_faculty_status TEXT NOT NULL DEFAULT 'UNKNOWN';

UPDATE professors
SET next_identity_check_at = faculty_checked_at + CASE
        WHEN faculty_status = 'VERIFIED' THEN INTERVAL '90 days'
        WHEN faculty_status = 'NOT_FACULTY' THEN INTERVAL '75 days'
        WHEN faculty_status = 'OUT_OF_SCOPE' THEN INTERVAL '90 days'
        WHEN faculty_status = 'CONFLICT' THEN INTERVAL '45 days'
        ELSE INTERVAL '30 days'
    END
WHERE next_identity_check_at IS NULL AND faculty_checked_at IS NOT NULL;

DO $$
BEGIN
    ALTER TABLE professors DROP CONSTRAINT IF EXISTS professors_faculty_status_check;
    ALTER TABLE professors ADD CONSTRAINT professors_faculty_status_check
        CHECK (faculty_status IN ('UNVERIFIED', 'VERIFIED', 'NOT_FACULTY', 'OUT_OF_SCOPE', 'CONFLICT', 'MANUAL_REVIEW'));
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'professors_faculty_confidence_check') THEN
        ALTER TABLE professors ADD CONSTRAINT professors_faculty_confidence_check
            CHECK (faculty_confidence BETWEEN 0 AND 1);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'professors_graduate_faculty_status_check') THEN
        ALTER TABLE professors ADD CONSTRAINT professors_graduate_faculty_status_check
            CHECK (graduate_faculty_status IN ('UNKNOWN', 'VERIFIED', 'NOT_LISTED'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS professors_faculty_status_idx
    ON professors (faculty_status, faculty_checked_at DESC);
CREATE INDEX IF NOT EXISTS professors_identity_refresh_idx
    ON professors (next_identity_check_at, faculty_status);

CREATE TABLE IF NOT EXISTS faculty_verification_evidence (
    id BIGSERIAL PRIMARY KEY,
    professor_id BIGINT NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    source_domain TEXT NOT NULL,
    observed_title TEXT,
    observed_institution TEXT,
    evidence_text TEXT,
    verification_status TEXT NOT NULL
        CHECK (verification_status IN ('UNVERIFIED', 'VERIFIED', 'NOT_FACULTY', 'OUT_OF_SCOPE', 'CONFLICT', 'MANUAL_REVIEW')),
    confidence NUMERIC(4, 3) NOT NULL DEFAULT 0
        CHECK (confidence BETWEEN 0 AND 1),
    decision_method TEXT,
    model_name TEXT,
    prompt_version INTEGER,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT faculty_verification_evidence_unique UNIQUE (professor_id, source_url)
);

ALTER TABLE faculty_verification_evidence
    ADD COLUMN IF NOT EXISTS decision_method TEXT;
ALTER TABLE faculty_verification_evidence
    ADD COLUMN IF NOT EXISTS model_name TEXT;
ALTER TABLE faculty_verification_evidence
    ADD COLUMN IF NOT EXISTS prompt_version INTEGER;
ALTER TABLE faculty_verification_evidence
    ADD COLUMN IF NOT EXISTS source_type TEXT NOT NULL DEFAULT 'UNKNOWN';
ALTER TABLE faculty_verification_evidence
    ADD COLUMN IF NOT EXISTS page_title TEXT;
ALTER TABLE faculty_verification_evidence
    ADD COLUMN IF NOT EXISTS role_category TEXT NOT NULL DEFAULT 'UNKNOWN';
ALTER TABLE faculty_verification_evidence
    ADD COLUMN IF NOT EXISTS observed_employer TEXT;
ALTER TABLE faculty_verification_evidence
    ADD COLUMN IF NOT EXISTS currentness TEXT NOT NULL DEFAULT 'UNKNOWN';
ALTER TABLE faculty_verification_evidence
    ADD COLUMN IF NOT EXISTS lookup_status TEXT NOT NULL DEFAULT 'FOUND';
ALTER TABLE faculty_verification_evidence
    ADD COLUMN IF NOT EXISTS evidence_excerpt TEXT;
ALTER TABLE faculty_verification_evidence
    ADD COLUMN IF NOT EXISTS extracted_text TEXT;
ALTER TABLE faculty_verification_evidence
    ADD COLUMN IF NOT EXISTS http_status INTEGER;
ALTER TABLE faculty_verification_evidence
    ADD COLUMN IF NOT EXISTS supports_decision BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE faculty_verification_evidence
    ADD COLUMN IF NOT EXISTS scope_status TEXT NOT NULL DEFAULT 'UNKNOWN';

DO $$
BEGIN
    ALTER TABLE faculty_verification_evidence
        DROP CONSTRAINT IF EXISTS faculty_verification_evidence_verification_status_check;
    ALTER TABLE faculty_verification_evidence
        ADD CONSTRAINT faculty_verification_evidence_verification_status_check
        CHECK (verification_status IN ('UNVERIFIED', 'VERIFIED', 'NOT_FACULTY', 'OUT_OF_SCOPE', 'CONFLICT', 'MANUAL_REVIEW'));
END $$;

CREATE INDEX IF NOT EXISTS faculty_verification_evidence_professor_idx
    ON faculty_verification_evidence (professor_id, checked_at DESC);

-- Gemini is an optional evidence extractor for cases that deterministic rules
-- cannot resolve. This shared counter keeps every worker inside an application-
-- controlled daily budget even after restarts.
CREATE TABLE IF NOT EXISTS ai_usage_daily (
    usage_date DATE NOT NULL,
    provider TEXT NOT NULL,
    feature TEXT NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0 CHECK (request_count >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (usage_date, provider, feature)
);

-- Version 1 accepted same-name directory/listing pages too easily. Preserve its
-- evidence for audit, but require those machine decisions to pass the stricter
-- verifier before they can return to public results.
UPDATE professors
SET faculty_status = 'MANUAL_REVIEW',
    faculty_confidence = 0,
    faculty_verified_at = NULL,
    updated_at = NOW()
WHERE faculty_verification_method = 'official_directory'
  AND faculty_verification_version < 2
  AND faculty_status = 'VERIFIED';

UPDATE faculty_verification_evidence fve
SET verification_status = 'MANUAL_REVIEW', confidence = 0
FROM professors p
WHERE fve.professor_id = p.id
  AND p.faculty_verification_method = 'official_directory'
  AND p.faculty_verification_version < 2
  AND fve.verification_status = 'VERIFIED';

-- Version 3 incorrectly treated a missing research keyword on an otherwise
-- valid official faculty profile as an identity conflict. Identity and topic
-- relevance are now independent. Do not auto-approve these records: make
-- them due for a safe recheck under the corrected verifier.
UPDATE professors p
SET faculty_status = 'UNVERIFIED',
    faculty_confidence = 0,
    faculty_verified_at = NULL,
    faculty_verification_version = 2,
    next_identity_check_at = NOW(),
    updated_at = NOW()
WHERE p.faculty_status = 'CONFLICT'
  AND EXISTS (
      SELECT 1
      FROM faculty_verification_evidence fve
      WHERE fve.professor_id = p.id
        AND fve.verification_status = 'CONFLICT'
        AND fve.evidence_text =
            'Official faculty page is for an unrelated research discipline.'
  );

UPDATE faculty_verification_evidence
SET verification_status = 'UNVERIFIED', confidence = 0, checked_at = NOW()
WHERE verification_status = 'CONFLICT'
  AND evidence_text =
      'Official faculty page is for an unrelated research discipline.';

CREATE TABLE IF NOT EXISTS professor_profiles (
    id BIGSERIAL PRIMARY KEY,
    professor_id BIGINT REFERENCES professors(id) ON DELETE SET NULL,
    owner_user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    institution_id BIGINT REFERENCES institutions(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    department TEXT,
    official_profile_url TEXT NOT NULL,
    verification_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (verification_status IN ('pending', 'verified', 'rejected', 'expired')),
    verified_at TIMESTAMPTZ,
    verification_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT professor_profiles_owner_unique UNIQUE (owner_user_id),
    CONSTRAINT professor_profiles_professor_unique UNIQUE (professor_id)
);

-- Preserve the stronger identity verification already performed for claimed
-- professor accounts.
UPDATE professors p
SET faculty_status = 'VERIFIED',
    faculty_title = pp.title,
    faculty_source_url = pp.official_profile_url,
    faculty_verification_method = 'manual_review',
    faculty_verification_version = 5,
    faculty_confidence = 1.0,
    faculty_checked_at = COALESCE(pp.verified_at, NOW()),
    faculty_verified_at = COALESCE(pp.verified_at, NOW()),
    next_identity_check_at = COALESCE(
        pp.verification_expires_at, pp.verified_at + INTERVAL '1 year',
        NOW() + INTERVAL '1 year'
    ),
    homepage_url = COALESCE(p.homepage_url, pp.official_profile_url),
    updated_at = NOW()
FROM professor_profiles pp
WHERE pp.professor_id = p.id
  AND pp.verification_status = 'verified';

-- Version 4 verifies career moves using an official current faculty page plus
-- corroborating OpenAlex affiliation fragments, an earlier institution named
-- on that page, or matching publication evidence. Existing positive decisions
-- remain valid; only unresolved automatic decisions need the new resolver.
UPDATE professors
SET faculty_verification_version = 4,
    updated_at = NOW()
WHERE faculty_verification_version = 3
  AND faculty_status IN ('VERIFIED', 'NOT_FACULTY')
  AND faculty_verification_method IS DISTINCT FROM 'manual_review';

-- The earlier verifier escalated one different-university search result to
-- CONFLICT. That is not a real ambiguity and should not consume staff time.
-- Keep the evidence for audit, but let version 4 retry it as an ordinary
-- unresolved identity. New conflicts require multiple plausible official
-- faculty profiles.
UPDATE professors p
SET faculty_status = 'UNVERIFIED',
    faculty_confidence = 0,
    faculty_verification_method = 'automatic_search',
    faculty_verified_at = NULL,
    next_identity_check_at = NOW(),
    updated_at = NOW()
WHERE p.faculty_status = 'CONFLICT'
  AND p.faculty_verification_version < 4
  AND EXISTS (
      SELECT 1
      FROM faculty_verification_evidence fve
      WHERE fve.professor_id = p.id
        AND fve.evidence_text =
            'The official faculty page does not match the candidate''s institution. This may be a different person with the same name.'
  );

UPDATE faculty_verification_evidence
SET verification_status = 'UNVERIFIED',
    confidence = 0,
    decision_method = 'automatic_search',
    evidence_text =
        'A possible official page was found, but the current evidence does not safely connect it to this OpenAlex author.',
    checked_at = NOW()
WHERE verification_status = 'CONFLICT'
  AND evidence_text =
      'The official faculty page does not match the candidate''s institution. This may be a different person with the same name.';

UPDATE professors
SET faculty_verification_version = 4,
    next_identity_check_at = NOW(),
    updated_at = NOW()
WHERE faculty_verification_version = 3
  AND faculty_status IN ('UNVERIFIED', 'CONFLICT')
  AND faculty_verification_method IS DISTINCT FROM 'manual_review';

-- Version 5 links ambiguous identities to exact papers/DOIs before accepting
-- a different current institution and recognizes common international
-- academic domains. Existing positive automatic decisions remain usable;
-- unresolved identities are scheduled for the stronger resolver.
UPDATE professors
SET faculty_verification_version = 5,
    updated_at = NOW()
WHERE faculty_verification_version = 4
  AND faculty_status IN ('VERIFIED', 'NOT_FACULTY')
  AND faculty_verification_method IS DISTINCT FROM 'manual_review';

UPDATE professors
SET next_identity_check_at = NOW(),
    updated_at = NOW()
WHERE faculty_verification_version < 5
  AND faculty_status IN ('UNVERIFIED', 'CONFLICT', 'MANUAL_REVIEW')
  AND faculty_verification_method IS DISTINCT FROM 'manual_review';

CREATE TABLE IF NOT EXISTS institution_memberships (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    institution_id BIGINT NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    department TEXT,
    official_profile_url TEXT NOT NULL,
    verification_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (verification_status IN ('pending', 'verified', 'rejected', 'expired')),
    verified_at TIMESTAMPTZ,
    verification_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT institution_memberships_user_unique UNIQUE (user_id)
);

CREATE TABLE IF NOT EXISTS role_verifications (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    professor_profile_id BIGINT REFERENCES professor_profiles(id) ON DELETE CASCADE,
    institution_membership_id BIGINT REFERENCES institution_memberships(id) ON DELETE CASCADE,
    method TEXT NOT NULL
        CHECK (method IN ('institution_email', 'official_directory', 'orcid', 'institution_admin', 'manual_review')),
    evidence_url TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'verified', 'rejected', 'expired')),
    reviewed_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
    reviewer_notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS opportunities (
    id BIGSERIAL PRIMARY KEY,
    professor_id BIGINT REFERENCES professors(id) ON DELETE SET NULL,
    submitted_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
    institution_id BIGINT REFERENCES institutions(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    institution_name TEXT NOT NULL,
    professor_name TEXT,
    research_area TEXT NOT NULL,
    position_type TEXT NOT NULL
        CHECK (position_type IN ('PhD', 'Postdoc', 'Research Assistant', 'Masters', 'Internship')),
    description TEXT NOT NULL,
    funding_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (funding_status IN ('confirmed', 'partial', 'unknown')),
    gpa_policy TEXT NOT NULL DEFAULT 'program_minimum'
        CHECK (gpa_policy IN ('no_lab_cutoff', 'program_minimum', 'exceptions_considered', 'holistic_review', 'not_stated')),
    international_eligible BOOLEAN,
    start_term TEXT,
    application_deadline DATE,
    application_url TEXT,
    source_kind TEXT NOT NULL
        CHECK (source_kind IN ('verified_post', 'public_signal', 'university_post')),
    organic_score INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('draft', 'pending', 'active', 'rejected', 'expired', 'closed')),
    published_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Existing installations receive the ranking column when this idempotent schema
-- is reapplied. Direct verified posts outrank public-web discoveries organically.
ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS organic_score INTEGER NOT NULL DEFAULT 0;
UPDATE opportunities
SET organic_score = CASE source_kind
    WHEN 'verified_post' THEN 100
    WHEN 'university_post' THEN 95
    WHEN 'public_signal' THEN 60
    ELSE 0
END
WHERE organic_score = 0;

CREATE TABLE IF NOT EXISTS opportunity_sources (
    id BIGSERIAL PRIMARY KEY,
    opportunity_id BIGINT NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    source_external_id TEXT UNIQUE,
    source_type TEXT NOT NULL
        CHECK (source_type IN ('professor_attestation', 'university_attestation', 'homepage', 'social', 'grant', 'manual')),
    source_url TEXT,
    evidence_text TEXT,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_checked_at TIMESTAMPTZ,
    confidence TEXT NOT NULL DEFAULT 'medium'
        CHECK (confidence IN ('low', 'medium', 'high'))
);

CREATE TABLE IF NOT EXISTS sponsorships (
    id BIGSERIAL PRIMARY KEY,
    opportunity_id BIGINT NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    sponsor_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'active', 'ended', 'refunded')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (ends_at > starts_at)
);

CREATE TABLE IF NOT EXISTS reports (
    id BIGSERIAL PRIMARY KEY,
    opportunity_id BIGINT NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    reporter_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'reviewed', 'dismissed', 'actioned')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS papers (
    id BIGSERIAL PRIMARY KEY,
    openalex_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    publication_year INTEGER,
    venue TEXT,
    citation_count INTEGER NOT NULL DEFAULT 0,
    doi TEXT,
    pdf_url TEXT,
    pdf_checked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE papers ADD COLUMN IF NOT EXISTS pdf_url TEXT;
ALTER TABLE papers ADD COLUMN IF NOT EXISTS pdf_checked_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS professor_papers (
    professor_id BIGINT NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    paper_id BIGINT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    author_position TEXT,
    raw_affiliation_text TEXT,
    affiliation_status TEXT NOT NULL DEFAULT 'NOT_CHECKED'
        CHECK (affiliation_status IN ('NOT_CHECKED', 'MATCHED', 'NOT_FOUND', 'UNAVAILABLE')),
    affiliation_text TEXT,
    affiliation_source_url TEXT,
    affiliation_institution TEXT,
    affiliation_email TEXT,
    affiliation_checked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (professor_id, paper_id)
);
ALTER TABLE professor_papers ADD COLUMN IF NOT EXISTS raw_affiliation_text TEXT;
ALTER TABLE professor_papers ADD COLUMN IF NOT EXISTS affiliation_status TEXT NOT NULL DEFAULT 'NOT_CHECKED';
ALTER TABLE professor_papers ADD COLUMN IF NOT EXISTS affiliation_text TEXT;
ALTER TABLE professor_papers ADD COLUMN IF NOT EXISTS affiliation_source_url TEXT;
ALTER TABLE professor_papers ADD COLUMN IF NOT EXISTS affiliation_institution TEXT;
ALTER TABLE professor_papers ADD COLUMN IF NOT EXISTS affiliation_email TEXT;
ALTER TABLE professor_papers ADD COLUMN IF NOT EXISTS affiliation_checked_at TIMESTAMPTZ;
ALTER TABLE professor_papers ADD COLUMN IF NOT EXISTS affiliation_version INTEGER NOT NULL DEFAULT 0;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'professor_papers_affiliation_status_check'
    ) THEN
        ALTER TABLE professor_papers
            ADD CONSTRAINT professor_papers_affiliation_status_check
            CHECK (affiliation_status IN ('NOT_CHECKED', 'MATCHED', 'NOT_FOUND', 'UNAVAILABLE'));
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS fundings (
    id BIGSERIAL PRIMARY KEY,
    professor_id BIGINT NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    funding_hash CHAR(64) NOT NULL UNIQUE,
    grant_title TEXT NOT NULL,
    grant_id TEXT,
    funder TEXT NOT NULL,
    amount NUMERIC(14, 2),
    award_date DATE,
    expiration_date DATE,
    source_url TEXT,
    research_domains TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE fundings
    ADD COLUMN IF NOT EXISTS research_domains TEXT[] NOT NULL DEFAULT '{}';

-- Funding freshness is topic- and source-specific. A Chemistry NSF check must
-- not suppress a later Political Science ORCID/NIH check for the same person.
CREATE TABLE IF NOT EXISTS professor_topic_grant_checks (
    professor_id BIGINT NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    radar_topic_id BIGINT NOT NULL REFERENCES radar_topics(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('CHECKED', 'NO_MATCH', 'NOT_APPLICABLE', 'SOURCE_UNAVAILABLE', 'DISABLED')),
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    next_check_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '30 days',
    last_error TEXT,
    PRIMARY KEY (professor_id, radar_topic_id, source)
);
CREATE INDEX IF NOT EXISTS professor_topic_grant_checks_due_idx
    ON professor_topic_grant_checks (radar_topic_id, next_check_at);

CREATE TABLE IF NOT EXISTS hiring_signals (
    id BIGSERIAL PRIMARY KEY,
    professor_id BIGINT NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    raw_text_hash CHAR(64) NOT NULL UNIQUE,
    signal_type TEXT NOT NULL,
    confidence TEXT NOT NULL CHECK (confidence IN ('low', 'medium', 'high')),
    raw_text TEXT NOT NULL,
    source_url TEXT NOT NULL,
    position_type TEXT NOT NULL DEFAULT 'PhD'
        CHECK (position_type IN ('PhD', 'Postdoc', 'Research Assistant', 'Internship')),
    attribution_status TEXT NOT NULL DEFAULT 'UNVERIFIED'
        CHECK (attribution_status IN ('VERIFIED', 'UNVERIFIED', 'CONFLICT')),
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_checked_at TIMESTAMPTZ,
    check_status TEXT NOT NULL DEFAULT 'PRESENT'
        CHECK (check_status IN ('PRESENT', 'NOT_FOUND', 'SOURCE_UNAVAILABLE')),
    consecutive_check_failures INTEGER NOT NULL DEFAULT 0,
    next_check_at TIMESTAMPTZ,
    source_date_text TEXT,
    source_date DATE,
    source_date_precision TEXT
        CHECK (source_date_precision IS NULL OR source_date_precision IN ('YEAR', 'MONTH', 'DAY', 'SEASON')),
    freshness_status TEXT NOT NULL DEFAULT 'UNDATED'
        CHECK (freshness_status IN ('CURRENT', 'UPCOMING', 'UNDATED', 'OLDER', 'HISTORICAL', 'EXPIRED')),
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE hiring_signals ADD COLUMN IF NOT EXISTS position_type TEXT NOT NULL DEFAULT 'PhD';
ALTER TABLE hiring_signals ADD COLUMN IF NOT EXISTS attribution_status TEXT NOT NULL DEFAULT 'UNVERIFIED';
ALTER TABLE hiring_signals ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE hiring_signals ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE hiring_signals ADD COLUMN IF NOT EXISTS check_status TEXT NOT NULL DEFAULT 'PRESENT';
ALTER TABLE hiring_signals ADD COLUMN IF NOT EXISTS consecutive_check_failures INTEGER NOT NULL DEFAULT 0;
ALTER TABLE hiring_signals ADD COLUMN IF NOT EXISTS next_check_at TIMESTAMPTZ;
ALTER TABLE hiring_signals ADD COLUMN IF NOT EXISTS source_date_text TEXT;
ALTER TABLE hiring_signals ADD COLUMN IF NOT EXISTS source_date DATE;
ALTER TABLE hiring_signals ADD COLUMN IF NOT EXISTS source_date_precision TEXT;
ALTER TABLE hiring_signals ADD COLUMN IF NOT EXISTS freshness_status TEXT NOT NULL DEFAULT 'UNDATED';

-- Automated web discoveries used to create pending opportunity advertisements.
-- They are evidence records, not submissions, so keep the evidence in
-- hiring_signals and prevent those legacy rows from entering moderation/public ads.
UPDATE opportunities
SET status = 'closed', updated_at = NOW()
WHERE source_kind = 'public_signal' AND status IN ('pending', 'active');

-- Shared, user-independent research indexes. A normalized topic is discovered
-- once and reused by every visitor; personal filters are applied at read time.
CREATE TABLE IF NOT EXISTS radar_topics (
    id BIGSERIAL PRIMARY KEY,
    topic_key CHAR(64) NOT NULL UNIQUE,
    requested_query TEXT NOT NULL,
    normalized_query TEXT NOT NULL,
    normalized_topic TEXT,
    status TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'indexing', 'partial', 'ready', 'failed')),
    desired_results INTEGER NOT NULL DEFAULT 100
        CHECK (desired_results BETWEEN 1 AND 100),
    discovery_version INTEGER NOT NULL DEFAULT 0,
    candidates_seen INTEGER NOT NULL DEFAULT 0,
    verified_count INTEGER NOT NULL DEFAULT 0,
    papers_found INTEGER NOT NULL DEFAULT 0,
    sources_exhausted BOOLEAN NOT NULL DEFAULT FALSE,
    search_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_indexed_at TIMESTAMPTZ,
    next_refresh_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE radar_topics
    ADD COLUMN IF NOT EXISTS discovery_version INTEGER NOT NULL DEFAULT 0;

-- Stable OpenAlex hierarchy nodes used by ScholarRadar. One user-facing
-- research area can map to several domains, fields, subfields, or topics.
CREATE TABLE IF NOT EXISTS openalex_research_nodes (
    openalex_id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL
        CHECK (node_type IN ('domain', 'field', 'subfield', 'topic')),
    display_name TEXT NOT NULL,
    description TEXT,
    parent_id TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS radar_topic_openalex_nodes (
    radar_topic_id BIGINT NOT NULL REFERENCES radar_topics(id) ON DELETE CASCADE,
    openalex_node_id TEXT NOT NULL
        REFERENCES openalex_research_nodes(openalex_id) ON DELETE CASCADE,
    weight NUMERIC(5, 3) NOT NULL DEFAULT 1.0,
    mapping_method TEXT NOT NULL,
    reviewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (radar_topic_id, openalex_node_id)
);

CREATE TABLE IF NOT EXISTS radar_topic_professors (
    radar_topic_id BIGINT NOT NULL REFERENCES radar_topics(id) ON DELETE CASCADE,
    professor_id BIGINT NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    result_rank INTEGER NOT NULL,
    research_score NUMERIC(5, 2) NOT NULL DEFAULT 0,
    matching_papers INTEGER NOT NULL DEFAULT 0,
    latest_paper_title TEXT,
    latest_paper_year INTEGER,
    latest_paper_url TEXT,
    is_current_match BOOLEAN NOT NULL DEFAULT TRUE,
    first_matched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_matched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (radar_topic_id, professor_id)
);
ALTER TABLE radar_topic_professors
    ADD COLUMN IF NOT EXISTS is_current_match BOOLEAN NOT NULL DEFAULT TRUE;

-- Exact paper evidence for each topic/professor match. professor_papers is the
-- professor's global publication trail; this table records which paper made a
-- particular professor match a particular radar topic.
CREATE TABLE IF NOT EXISTS radar_topic_professor_papers (
    radar_topic_id BIGINT NOT NULL REFERENCES radar_topics(id) ON DELETE CASCADE,
    professor_id BIGINT NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    paper_id BIGINT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    relevance_score NUMERIC(5, 2) NOT NULL DEFAULT 0,
    matched_query TEXT NOT NULL,
    matched_openalex_node_id TEXT,
    is_current_match BOOLEAN NOT NULL DEFAULT TRUE,
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_matched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (radar_topic_id, professor_id, paper_id),
    FOREIGN KEY (professor_id, paper_id)
        REFERENCES professor_papers(professor_id, paper_id) ON DELETE CASCADE
);
ALTER TABLE radar_topic_professor_papers
    ADD COLUMN IF NOT EXISTS matched_openalex_node_id TEXT;

-- Durable work queue. The partial unique index prevents duplicate active work
-- for the same topic/professor while allowing a later refresh job.
CREATE TABLE IF NOT EXISTS radar_jobs (
    id BIGSERIAL PRIMARY KEY,
    radar_topic_id BIGINT REFERENCES radar_topics(id) ON DELETE CASCADE,
    professor_id BIGINT REFERENCES professors(id) ON DELETE CASCADE,
    requested_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
    job_type TEXT NOT NULL CHECK (job_type IN (
        'DISCOVER_CANDIDATES', 'VERIFY_FACULTY', 'REFRESH_FACULTY',
        'CHECK_HIRING', 'CHECK_GRANTS', 'ENRICH_PROFESSORS',
        'REINDEX_RESEARCH'
    )),
    dedupe_key TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 50 CHECK (priority BETWEEN 0 AND 100),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts BETWEEN 1 AND 20),
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    locked_at TIMESTAMPTZ,
    locked_by TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    last_error TEXT,
    result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS radar_worker_heartbeats (
    worker_id TEXT PRIMARY KEY,
    process_id INTEGER,
    hostname TEXT,
    current_job_id BIGINT REFERENCES radar_jobs(id) ON DELETE SET NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    stopped_at TIMESTAMPTZ
);

-- Successful web searches are shared by worker jobs and survive restarts.
-- Empty/error responses are deliberately not cached.
CREATE TABLE IF NOT EXISTS web_search_cache (
    query_key CHAR(64) PRIMARY KEY,
    normalized_query TEXT NOT NULL,
    results_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    provider_names TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    searched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- Provider health is shared across isolated worker job processes. Without a
-- durable circuit breaker, each new job would immediately retry an engine that
-- the previous job had just discovered was blocked or unavailable.
CREATE TABLE IF NOT EXISTS web_search_provider_health (
    provider_name TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'healthy'
        CHECK (status IN ('healthy', 'blocked')),
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    blocked_until TIMESTAMPTZ,
    next_request_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_failure_at TIMESTAMPTZ,
    last_error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Migrations for databases created before the staged indexing pipeline and
-- shared OpenAlex rate limiter were introduced.
ALTER TABLE radar_jobs
    DROP CONSTRAINT IF EXISTS radar_jobs_job_type_check;
ALTER TABLE radar_jobs
    ADD CONSTRAINT radar_jobs_job_type_check CHECK (job_type IN (
        'DISCOVER_CANDIDATES', 'VERIFY_FACULTY', 'REFRESH_FACULTY',
        'CHECK_HIRING', 'CHECK_GRANTS', 'ENRICH_PROFESSORS',
        'REINDEX_RESEARCH'
    ));
ALTER TABLE web_search_provider_health
    ADD COLUMN IF NOT EXISTS next_request_at TIMESTAMPTZ;
ALTER TABLE web_search_provider_health ADD COLUMN IF NOT EXISTS usage_day DATE;
ALTER TABLE web_search_provider_health ADD COLUMN IF NOT EXISTS remote_remaining INTEGER;
ALTER TABLE web_search_provider_health ADD COLUMN IF NOT EXISTS remote_checked_at TIMESTAMPTZ;
ALTER TABLE web_search_provider_health ADD COLUMN IF NOT EXISTS remote_reset_at TIMESTAMPTZ;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS identity_search_pending BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE web_search_provider_health ADD COLUMN IF NOT EXISTS usage_month DATE;
ALTER TABLE web_search_provider_health ADD COLUMN IF NOT EXISTS requests_this_month INTEGER NOT NULL DEFAULT 0;
ALTER TABLE web_search_provider_health ADD COLUMN IF NOT EXISTS requests_total BIGINT NOT NULL DEFAULT 0;
ALTER TABLE web_search_provider_health ADD COLUMN IF NOT EXISTS requests_today INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS identity_orcid_cache (
    orcid_id TEXT PRIMARY KEY,
    result_json JSONB NOT NULL DEFAULT '{}',
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- A run is targeted to one user query. ScholarRadar never attempts to preload
-- every professor or every field.
CREATE TABLE IF NOT EXISTS radar_runs (
    id BIGSERIAL PRIMARY KEY,
    query_key CHAR(64) NOT NULL,
    requested_query TEXT NOT NULL,
    normalized_topic TEXT,
    requested_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'completed', 'exhausted', 'waiting', 'failed', 'cancelled')),
    stage TEXT NOT NULL DEFAULT 'Starting radar',
    progress INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    professors_found INTEGER NOT NULL DEFAULT 0,
    papers_found INTEGER NOT NULL DEFAULT 0,
    candidates_ranked INTEGER NOT NULL DEFAULT 0,
    faculty_identities_checked INTEGER NOT NULL DEFAULT 0,
    professors_checked INTEGER NOT NULL DEFAULT 0,
    grants_added INTEGER NOT NULL DEFAULT 0,
    signals_added INTEGER NOT NULL DEFAULT 0,
    max_papers INTEGER NOT NULL DEFAULT 10 CHECK (max_papers BETWEEN 1 AND 100),
    target_professors INTEGER NOT NULL DEFAULT 25
        CHECK (target_professors IN (10, 25, 50, 100)),
    web_check_limit INTEGER NOT NULL DEFAULT 12
        CHECK (web_check_limit BETWEEN 1 AND 25),
    error_message TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE radar_runs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE radar_runs ADD COLUMN IF NOT EXISTS target_professors INTEGER NOT NULL DEFAULT 25;
ALTER TABLE radar_runs ADD COLUMN IF NOT EXISTS web_check_limit INTEGER NOT NULL DEFAULT 12;
ALTER TABLE radar_runs ADD COLUMN IF NOT EXISTS candidates_ranked INTEGER NOT NULL DEFAULT 0;
ALTER TABLE radar_runs ADD COLUMN IF NOT EXISTS faculty_identities_checked INTEGER NOT NULL DEFAULT 0;
DO $$
BEGIN
    ALTER TABLE radar_runs DROP CONSTRAINT IF EXISTS radar_runs_status_check;
    ALTER TABLE radar_runs ADD CONSTRAINT radar_runs_status_check
        CHECK (status IN ('running', 'completed', 'exhausted', 'waiting', 'failed', 'cancelled'));
END $$;

-- Every professor discovered for a run is retained, even if no explicit hiring
-- statement is found. Hiring evidence and probable-opportunity signals are
-- displayed as separate confidence categories in the UI.
CREATE TABLE IF NOT EXISTS radar_run_professors (
    radar_run_id BIGINT NOT NULL REFERENCES radar_runs(id) ON DELETE CASCADE,
    professor_id BIGINT NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    result_rank INTEGER NOT NULL,
    research_score NUMERIC(5, 2) NOT NULL DEFAULT 0,
    matching_papers INTEGER NOT NULL DEFAULT 0,
    latest_paper_title TEXT,
    latest_paper_year INTEGER,
    latest_paper_url TEXT,
    grant_sources_checked BOOLEAN NOT NULL DEFAULT FALSE,
    public_sources_checked BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (radar_run_id, professor_id)
);
ALTER TABLE radar_run_professors
    ADD COLUMN IF NOT EXISTS grant_sources_checked BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE radar_run_professors
    ADD COLUMN IF NOT EXISTS public_sources_checked BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS radar_run_results (
    radar_run_id BIGINT NOT NULL REFERENCES radar_runs(id) ON DELETE CASCADE,
    opportunity_id BIGINT NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (radar_run_id, opportunity_id)
);

CREATE INDEX IF NOT EXISTS opportunities_active_search_idx
    ON opportunities (status, position_type, research_area, application_deadline);
CREATE INDEX IF NOT EXISTS opportunities_institution_idx ON opportunities (institution_name);
CREATE INDEX IF NOT EXISTS professor_profiles_status_idx ON professor_profiles (verification_status);
CREATE INDEX IF NOT EXISTS institution_memberships_status_idx ON institution_memberships (verification_status);
CREATE INDEX IF NOT EXISTS role_verifications_status_idx ON role_verifications (status);
CREATE INDEX IF NOT EXISTS site_admins_active_idx ON site_admins (admin_role) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS admin_audit_log_created_idx ON admin_audit_log (created_at DESC);
CREATE INDEX IF NOT EXISTS professors_domain_idx ON professors (research_domain);
CREATE INDEX IF NOT EXISTS professors_score_idx ON professors (radar_score DESC);
CREATE INDEX IF NOT EXISTS hiring_signals_professor_idx ON hiring_signals (professor_id);
CREATE INDEX IF NOT EXISTS opportunities_organic_score_idx
    ON opportunities (status, organic_score DESC, published_at DESC);
CREATE INDEX IF NOT EXISTS radar_runs_query_cache_idx
    ON radar_runs (query_key, created_at DESC);
CREATE INDEX IF NOT EXISTS radar_runs_status_idx ON radar_runs (status, created_at DESC);
CREATE INDEX IF NOT EXISTS radar_run_professors_rank_idx
    ON radar_run_professors (radar_run_id, result_rank);
CREATE INDEX IF NOT EXISTS radar_topics_requested_idx
    ON radar_topics (last_requested_at DESC);
CREATE INDEX IF NOT EXISTS radar_topics_refresh_idx
    ON radar_topics (next_refresh_at, status);
CREATE INDEX IF NOT EXISTS radar_topic_professors_rank_idx
    ON radar_topic_professors (radar_topic_id, is_current_match, result_rank);
CREATE INDEX IF NOT EXISTS radar_topic_professors_current_rank_idx
    ON radar_topic_professors (radar_topic_id, result_rank)
    WHERE is_current_match = TRUE;
CREATE INDEX IF NOT EXISTS radar_topic_professor_papers_current_idx
    ON radar_topic_professor_papers (
        radar_topic_id, professor_id, relevance_score DESC
    )
    WHERE is_current_match = TRUE;
CREATE INDEX IF NOT EXISTS radar_jobs_claim_idx
    ON radar_jobs (status, available_at, priority DESC, created_at)
    WHERE status = 'queued';
CREATE UNIQUE INDEX IF NOT EXISTS radar_jobs_active_dedupe_idx
    ON radar_jobs (dedupe_key)
    WHERE status IN ('queued', 'running');

-- Topics containing decisions reset by the identity/relevance migration are
-- eligible for immediate background refresh instead of waiting 30 days.
UPDATE radar_topics topic
SET next_refresh_at = NOW(), updated_at = NOW()
WHERE EXISTS (
    SELECT 1
    FROM radar_topic_professors rtp
    JOIN professors p ON p.id = rtp.professor_id
    WHERE rtp.radar_topic_id = topic.id
      AND p.faculty_status = 'UNVERIFIED'
      AND p.next_identity_check_at <= NOW()
);
CREATE INDEX IF NOT EXISTS radar_worker_last_seen_idx
    ON radar_worker_heartbeats (last_seen_at DESC);
CREATE INDEX IF NOT EXISTS web_search_cache_expiry_idx
    ON web_search_cache (expires_at);
CREATE INDEX IF NOT EXISTS web_search_provider_block_idx
    ON web_search_provider_health (blocked_until)
    WHERE status = 'blocked';

-- Latest bounded identity pass: staff-only snippets, page reasons and affiliation trail.
ALTER TABLE professors ADD COLUMN IF NOT EXISTS identity_search_audit JSONB NOT NULL DEFAULT '{}'::jsonb;
