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

-- Canonical institution identity is separate from names seen on web pages.
-- Aliases are explicit and reviewable; parent systems never imply that two
-- campuses are interchangeable.
CREATE TABLE IF NOT EXISTS institution_aliases (
    institution_id BIGINT NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    alias_type TEXT NOT NULL DEFAULT 'OFFICIAL',
    reviewed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (institution_id, normalized_alias)
);
CREATE INDEX IF NOT EXISTS institution_aliases_normalized_idx
    ON institution_aliases (normalized_alias);

CREATE TABLE IF NOT EXISTS institution_domains (
    institution_id BIGINT NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
    domain TEXT NOT NULL,
    domain_type TEXT NOT NULL DEFAULT 'PRIMARY',
    reviewed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (institution_id, domain)
);
CREATE UNIQUE INDEX IF NOT EXISTS institution_domains_reviewed_unique_idx
    ON institution_domains (domain) WHERE reviewed = TRUE;

ALTER TABLE institutions ADD COLUMN IF NOT EXISTS parent_system_id BIGINT
    REFERENCES institutions(id) ON DELETE SET NULL;
ALTER TABLE institutions ADD COLUMN IF NOT EXISTS ror_id TEXT;
ALTER TABLE institutions ADD COLUMN IF NOT EXISTS operating_status TEXT NOT NULL DEFAULT 'UNKNOWN';
ALTER TABLE institutions ADD COLUMN IF NOT EXISTS faculty_discovery_status TEXT NOT NULL DEFAULT 'NOT_CHECKED';
ALTER TABLE institutions ADD COLUMN IF NOT EXISTS faculty_discovery_checked_at TIMESTAMPTZ;
ALTER TABLE institutions ADD COLUMN IF NOT EXISTS faculty_discovery_next_at TIMESTAMPTZ;
ALTER TABLE institutions ADD COLUMN IF NOT EXISTS faculty_discovery_error TEXT;

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
    CONSTRAINT professors_name_institution_unique UNIQUE (name, institution_name)
);

ALTER TABLE professors ADD COLUMN IF NOT EXISTS canonical_name_key TEXT;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS canonical_profile_url TEXT;
CREATE INDEX IF NOT EXISTS professors_canonical_name_institution_idx
    ON professors (institution_id, canonical_name_key)
    WHERE canonical_name_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS professors_canonical_profile_idx
    ON professors (canonical_profile_url)
    WHERE canonical_profile_url IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS professors_canonical_profile_unique_idx
    ON professors (canonical_profile_url)
    WHERE canonical_profile_url IS NOT NULL;
CREATE TABLE IF NOT EXISTS professor_name_aliases (
    professor_id BIGINT NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    canonical_name_key TEXT NOT NULL,
    source_url TEXT,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (professor_id, alias)
);
CREATE INDEX IF NOT EXISTS professor_name_aliases_key_idx
    ON professor_name_aliases (canonical_name_key);

CREATE TABLE IF NOT EXISTS professor_identity_review_queue (
    id BIGSERIAL PRIMARY KEY,
    professor_ids BIGINT[] NOT NULL,
    canonical_name_key TEXT,
    institution_id BIGINT REFERENCES institutions(id) ON DELETE SET NULL,
    reason TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'MERGED', 'SEPARATE', 'DISMISSED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ
);
DELETE FROM professor_identity_review_queue duplicate
USING professor_identity_review_queue keeper
WHERE duplicate.id > keeper.id
  AND duplicate.status = 'PENDING' AND keeper.status = 'PENDING'
  AND duplicate.canonical_name_key IS NOT DISTINCT FROM keeper.canonical_name_key
  AND duplicate.institution_id IS NOT DISTINCT FROM keeper.institution_id
  AND duplicate.reason = keeper.reason
  AND duplicate.professor_ids = keeper.professor_ids;
DROP INDEX IF EXISTS professor_identity_review_pending_unique;
CREATE UNIQUE INDEX professor_identity_review_pending_unique
    ON professor_identity_review_queue (
        canonical_name_key, COALESCE(institution_id, 0), reason, professor_ids
    ) WHERE status = 'PENDING';

ALTER TABLE professors ADD COLUMN IF NOT EXISTS faculty_status TEXT NOT NULL DEFAULT 'UNVERIFIED';
ALTER TABLE professors ADD COLUMN IF NOT EXISTS faculty_title TEXT;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS faculty_source_url TEXT;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS faculty_verification_method TEXT;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS faculty_verification_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS faculty_confidence NUMERIC(4, 3) NOT NULL DEFAULT 0;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS faculty_checked_at TIMESTAMPTZ;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS faculty_verified_at TIMESTAMPTZ;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS next_identity_check_at TIMESTAMPTZ;
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
ALTER TABLE professors ADD COLUMN IF NOT EXISTS data_origin TEXT NOT NULL DEFAULT 'OFFICIAL_DIRECTORY';
ALTER TABLE professors ADD COLUMN IF NOT EXISTS employment_status TEXT NOT NULL DEFAULT 'UNKNOWN';
ALTER TABLE professors ADD COLUMN IF NOT EXISTS canonical_rank TEXT;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS display_title TEXT;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS department TEXT;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS roster_verified_at TIMESTAMPTZ;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS roster_last_seen_at TIMESTAMPTZ;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS lab_gpa_check_status TEXT NOT NULL DEFAULT 'NOT_CHECKED';

-- Do not infer an official-directory membership from a legacy method label.
-- Canonical origins are reconciled below, after the evidence and membership
-- tables exist.

UPDATE professors
SET lab_gpa_check_status = CASE
        WHEN lab_gpa_evidence_text IS NOT NULL THEN 'FOUND'
        WHEN gpa_last_checked_at IS NOT NULL THEN 'NOT_STATED'
        ELSE 'NOT_CHECKED'
    END
WHERE lab_gpa_check_status = 'NOT_CHECKED';

CREATE TABLE IF NOT EXISTS faculty_directories (
    id BIGSERIAL PRIMARY KEY,
    institution_id BIGINT NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
    department TEXT NOT NULL,
    directory_url TEXT NOT NULL UNIQUE,
    directory_type TEXT NOT NULL DEFAULT 'DEPARTMENT_FACULTY',
    parser_type TEXT NOT NULL DEFAULT 'GENERIC_HTML',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    validation_status TEXT NOT NULL DEFAULT 'PENDING',
    validation_reason TEXT,
    discovered_by TEXT NOT NULL DEFAULT 'MANUAL',
    expected_profile_count INTEGER,
    content_hash CHAR(64),
    last_success_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- Discovery and extraction are deliberately staged. An official-domain URL
-- is not a roster, and a person-looking link is not yet a professor.
CREATE TABLE IF NOT EXISTS institution_units (
    id BIGSERIAL PRIMARY KEY,
    institution_id BIGINT NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
    parent_unit_id BIGINT REFERENCES institution_units(id) ON DELETE SET NULL,
    unit_name TEXT NOT NULL,
    unit_type TEXT NOT NULL CHECK (unit_type IN (
        'UNIVERSITY', 'COLLEGE', 'SCHOOL', 'DEPARTMENT', 'PROGRAM',
        'INSTITUTE', 'CENTER', 'LAB', 'ADMINISTRATIVE_UNIT', 'UNKNOWN'
    )),
    official_url TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (institution_id, official_url)
);
CREATE TABLE IF NOT EXISTS faculty_page_candidates (
    id BIGSERIAL PRIMARY KEY,
    institution_id BIGINT NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
    unit_id BIGINT REFERENCES institution_units(id) ON DELETE SET NULL,
    candidate_url TEXT NOT NULL,
    final_url TEXT,
    discovery_method TEXT NOT NULL,
    page_type TEXT NOT NULL DEFAULT 'UNKNOWN',
    scope_label TEXT,
    classification_status TEXT NOT NULL DEFAULT 'UNVALIDATED' CHECK (
        classification_status IN ('UNVALIDATED', 'APPROVED_ROSTER',
        'MIXED_ROSTER_REQUIRES_SECTION_PARSER', 'NOT_A_ROSTER',
        'UNCERTAIN_REQUIRES_REVIEW', 'FETCH_FAILED', 'REDIRECTED',
        'OUTSIDE_OFFICIAL_DOMAIN')),
    classification_reason TEXT,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    checked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (institution_id, candidate_url)
);
ALTER TABLE faculty_directories ADD COLUMN IF NOT EXISTS unit_id BIGINT
    REFERENCES institution_units(id) ON DELETE SET NULL;
ALTER TABLE faculty_directories ADD COLUMN IF NOT EXISTS page_candidate_id BIGINT
    REFERENCES faculty_page_candidates(id) ON DELETE SET NULL;
ALTER TABLE faculty_directories ADD COLUMN IF NOT EXISTS page_type TEXT NOT NULL DEFAULT 'UNKNOWN';
ALTER TABLE faculty_directories ADD COLUMN IF NOT EXISTS validation_status TEXT NOT NULL DEFAULT 'PENDING';
ALTER TABLE faculty_directories ADD COLUMN IF NOT EXISTS validation_reason TEXT;
ALTER TABLE faculty_directories ADD COLUMN IF NOT EXISTS discovered_by TEXT NOT NULL DEFAULT 'MANUAL';
ALTER TABLE faculty_directories ADD COLUMN IF NOT EXISTS expected_profile_count INTEGER;
ALTER TABLE faculty_directories DROP CONSTRAINT IF EXISTS faculty_directories_validation_status_check;
ALTER TABLE faculty_directories ADD CONSTRAINT faculty_directories_validation_status_check
    CHECK (validation_status IN ('PENDING', 'APPROVED', 'REJECTED', 'NEEDS_REVIEW'));

CREATE TABLE IF NOT EXISTS faculty_directory_memberships (
    professor_id BIGINT NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    directory_id BIGINT NOT NULL REFERENCES faculty_directories(id) ON DELETE CASCADE,
    listed_name TEXT NOT NULL,
    listed_title TEXT,
    listed_department TEXT,
    profile_url TEXT NOT NULL,
    appointment_type TEXT NOT NULL DEFAULT 'PRIMARY',
    source_excerpt TEXT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    currently_listed BOOLEAN NOT NULL DEFAULT TRUE,
    missing_checks INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (directory_id, profile_url)
);
CREATE INDEX IF NOT EXISTS faculty_directory_memberships_professor_idx
    ON faculty_directory_memberships (professor_id, currently_listed);

CREATE TABLE IF NOT EXISTS roster_member_candidates (
    id BIGSERIAL PRIMARY KEY,
    directory_id BIGINT NOT NULL REFERENCES faculty_directories(id) ON DELETE CASCADE,
    displayed_name TEXT NOT NULL,
    canonical_name_key TEXT NOT NULL,
    displayed_title TEXT,
    department TEXT,
    profile_url TEXT NOT NULL,
    canonical_profile_url TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    office_address TEXT,
    section_heading TEXT,
    appointment_type TEXT NOT NULL DEFAULT 'PRIMARY',
    source_excerpt TEXT,
    validation_status TEXT NOT NULL DEFAULT 'PENDING' CHECK (
        validation_status IN ('PENDING', 'PROFILE_VERIFIED', 'ROSTER_VERIFIED',
        'ROSTER_CONFIRMED_PROFILE_UNAVAILABLE', 'NAME_MISMATCH', 'ROLE_UNCLEAR',
        'INSTITUTION_UNRESOLVED', 'HISTORICAL_PROFILE', 'NOT_A_PERSON',
        'REJECTED', 'NEEDS_REVIEW')),
    validation_reason TEXT,
    profile_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    validation_version INTEGER NOT NULL DEFAULT 3,
    professor_id BIGINT REFERENCES professors(id) ON DELETE SET NULL,
    checked_at TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (directory_id, canonical_profile_url)
);
ALTER TABLE roster_member_candidates DROP CONSTRAINT IF EXISTS roster_member_candidates_validation_status_check;
ALTER TABLE roster_member_candidates ADD COLUMN IF NOT EXISTS validation_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE roster_member_candidates ALTER COLUMN validation_version SET DEFAULT 3;
ALTER TABLE roster_member_candidates ADD CONSTRAINT roster_member_candidates_validation_status_check
    CHECK (validation_status IN ('PENDING', 'PROFILE_VERIFIED', 'ROSTER_VERIFIED',
        'ROSTER_CONFIRMED_PROFILE_UNAVAILABLE', 'NAME_MISMATCH', 'ROLE_UNCLEAR',
        'INSTITUTION_UNRESOLVED', 'HISTORICAL_PROFILE', 'NOT_A_PERSON',
        'REJECTED', 'NEEDS_REVIEW', 'NOT_GROUP_LEADING_FACULTY'));
UPDATE faculty_directories directory
SET last_success_at = NULL, updated_at = NOW()
WHERE EXISTS (
    SELECT 1 FROM roster_member_candidates candidate
    WHERE candidate.directory_id=directory.id
      AND candidate.validation_version < 3
);
CREATE INDEX IF NOT EXISTS roster_member_candidates_status_idx
    ON roster_member_candidates (validation_status, checked_at);

-- Ollama is an evidence interpreter only. Its output is cached and audited
-- here; canonical records are still written by deterministic validation code.
CREATE TABLE IF NOT EXISTS ollama_extraction_runs (
    id BIGSERIAL PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_record_key TEXT NOT NULL,
    institution_id BIGINT REFERENCES institutions(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    input_excerpt TEXT NOT NULL,
    raw_response TEXT,
    parsed_response JSONB NOT NULL DEFAULT '{}'::jsonb,
    validation_status TEXT NOT NULL CHECK (
        validation_status IN (
            'VALID', 'INVALID_EVIDENCE', 'INVALID_RESPONSE', 'MODEL_UNAVAILABLE'
        )
    ),
    validation_errors TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_type, source_record_key, model, prompt_version, input_hash)
);
-- Keep the validation-status constraint aligned with every status that the
-- Ollama evidence layer can persist.  This ALTER is required for databases
-- created by an older schema because CREATE TABLE IF NOT EXISTS does not
-- update an existing CHECK constraint.
ALTER TABLE ollama_extraction_runs
    DROP CONSTRAINT IF EXISTS ollama_extraction_runs_validation_status_check;
ALTER TABLE ollama_extraction_runs
    ADD CONSTRAINT ollama_extraction_runs_validation_status_check
    CHECK (validation_status IN (
        'VALID', 'INVALID_EVIDENCE', 'INVALID_RESPONSE', 'MODEL_UNAVAILABLE'
    ));

CREATE INDEX IF NOT EXISTS ollama_extraction_runs_source_idx
    ON ollama_extraction_runs (source_type, source_record_key, created_at DESC);

CREATE TABLE IF NOT EXISTS faculty_appointments (
    id BIGSERIAL PRIMARY KEY,
    professor_id BIGINT NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    institution_id BIGINT NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    canonical_rank TEXT,
    appointment_type TEXT NOT NULL,
    country_code CHAR(2),
    start_date DATE,
    end_date DATE,
    current BOOLEAN NOT NULL DEFAULT TRUE,
    primary_appointment BOOLEAN NOT NULL DEFAULT FALSE,
    source_url TEXT NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (professor_id, institution_id, title, source_url)
);

CREATE TABLE IF NOT EXISTS professor_external_identities (
    professor_id BIGINT NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    confidence NUMERIC(4, 3) NOT NULL DEFAULT 0,
    match_status TEXT NOT NULL DEFAULT 'PENDING',
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    reviewed_at TIMESTAMPTZ,
    PRIMARY KEY (provider, external_id)
);

ALTER TABLE professors ADD COLUMN IF NOT EXISTS publication_status TEXT NOT NULL DEFAULT 'NOT_CHECKED';
ALTER TABLE professors ADD COLUMN IF NOT EXISTS publication_checked_at TIMESTAMPTZ;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS publication_discovery_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE professors DROP CONSTRAINT IF EXISTS professors_publication_status_check;
ALTER TABLE professors ADD CONSTRAINT professors_publication_status_check CHECK (
    publication_status IN ('NOT_CHECKED','OFFICIAL_PUBLICATIONS_FOUND','SCHOLAR_VERIFIED',
    'NO_PUBLICATIONS_FOUND','SOURCE_UNAVAILABLE','REVIEW_REQUIRED',
    'SCHOLAR_REVIEW_QUEUED','NOT_APPLICABLE'));
CREATE TABLE IF NOT EXISTS professor_publication_sources (
    id BIGSERIAL PRIMARY KEY,
    professor_id BIGINT NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL CHECK (source_type IN (
        'OFFICIAL_PROFILE','OFFICIAL_ALTERNATE_PROFILE','PERSONAL_SITE','LAB_SITE',
        'LINKED_SITE','GOOGLE_SCHOLAR')),
    source_url TEXT NOT NULL,
    identity_status TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (professor_id, source_url)
);
ALTER TABLE professor_publication_sources
    DROP CONSTRAINT IF EXISTS professor_publication_sources_source_type_check;
ALTER TABLE professor_publication_sources
    ADD CONSTRAINT professor_publication_sources_source_type_check CHECK (
        source_type IN ('OFFICIAL_PROFILE','OFFICIAL_ALTERNATE_PROFILE',
        'PERSONAL_SITE','LAB_SITE','LINKED_SITE','GOOGLE_SCHOLAR'));

-- Ambiguous Google Scholar rows are never attached as papers automatically.
-- They are persisted here so staff can explicitly accept or reject them.
CREATE TABLE IF NOT EXISTS scholar_publication_review_queue (
    id BIGSERIAL PRIMARY KEY,
    professor_id BIGINT NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    scholar_url TEXT NOT NULL,
    scholar_key TEXT NOT NULL,
    source_key CHAR(64) NOT NULL,
    title TEXT NOT NULL,
    publication_year INTEGER,
    authors TEXT,
    venue TEXT,
    evidence TEXT,
    model_decision TEXT,
    model_confidence NUMERIC(4, 3) CHECK (model_confidence BETWEEN 0 AND 1),
    model_reason TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (
        status IN ('PENDING','ACCEPTED','REJECTED','DISMISSED')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ,
    reviewed_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
    UNIQUE (professor_id, scholar_key, source_key)
);
CREATE INDEX IF NOT EXISTS scholar_publication_review_pending_idx
    ON scholar_publication_review_queue (status, created_at DESC)
    WHERE status='PENDING';

-- Research interests are fallback evidence only. They never create a faculty
-- identity and never claim that a publication exists. Each label retains the
-- official page and the exact profile passage from which it was extracted.
CREATE TABLE IF NOT EXISTS professor_research_interests (
    id BIGSERIAL PRIMARY KEY,
    professor_id BIGINT NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    display_interest TEXT NOT NULL,
    normalized_interest TEXT NOT NULL,
    evidence_method TEXT NOT NULL CHECK (
        evidence_method IN ('EXPLICIT_PROFILE_SECTION','QWEN_BIO_SUMMARY','QWEN_VALIDATED_SECTION',
                            'QWEN_PAPER_SUMMARY','MANUAL_REVIEW','EXPLICIT_DIRECTORY_FIELD')
    ),
    source_url TEXT NOT NULL,
    source_excerpt TEXT NOT NULL,
    confidence NUMERIC(4, 3) NOT NULL DEFAULT 0.700
        CHECK (confidence BETWEEN 0 AND 1),
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (professor_id, normalized_interest, source_url)
);
CREATE INDEX IF NOT EXISTS professor_research_interests_professor_idx
    ON professor_research_interests (professor_id, checked_at DESC);
ALTER TABLE professor_research_interests DROP CONSTRAINT IF EXISTS professor_research_interests_evidence_method_check;
ALTER TABLE professor_research_interests ADD CONSTRAINT professor_research_interests_evidence_method_check
    CHECK (evidence_method IN ('EXPLICIT_PROFILE_SECTION','QWEN_BIO_SUMMARY',
                              'QWEN_VALIDATED_SECTION','QWEN_PAPER_SUMMARY','MANUAL_REVIEW','EXPLICIT_DIRECTORY_FIELD'));

-- A professor-level research profile is the fast discovery layer. It is built
-- from explicit website interests first, then verified paper titles, then a
-- subject-local biography. Unsupported department-only guesses are forbidden.
ALTER TABLE professors ADD COLUMN IF NOT EXISTS research_profile_status TEXT NOT NULL DEFAULT 'NOT_CHECKED';
ALTER TABLE professors ADD COLUMN IF NOT EXISTS research_profile_primary_field TEXT;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS research_profile_source_url TEXT;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS research_profile_confidence NUMERIC(4,3) NOT NULL DEFAULT 0;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS research_profile_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS research_profile_checked_at TIMESTAMPTZ;
ALTER TABLE professors DROP CONSTRAINT IF EXISTS professors_research_profile_status_check;
ALTER TABLE professors ADD CONSTRAINT professors_research_profile_status_check CHECK (
    research_profile_status IN (
        'NOT_CHECKED','OFFICIAL_INTERESTS','PAPER_DERIVED','BIOGRAPHY_DERIVED',
        'AWAITING_MODEL','AWAITING_PUBLICATIONS','MANUAL_REVIEW_REQUIRED','MANUAL_REVIEWED'
    )
);

-- Earlier builds could create unsupported department-only AI suggestions.
-- They are intentionally discarded so every remaining profile is grounded in
-- an explicit page statement, a biography, verified paper titles, or staff review.
DELETE FROM professor_research_interests WHERE evidence_method='AI_SUGGESTION';

-- Research profile build version 3 adds authoritative structured directory research fields.
-- Old automatic profile rows are retained temporarily for auditability, but
-- they are stale and must not be searched or treated as authoritative until
-- the current pipeline successfully replaces them. Manual staff profiles are
-- trusted and can be promoted directly to the current version.
UPDATE professors p
SET research_profile_status='MANUAL_REVIEWED',
    research_profile_confidence=1.0,
    research_profile_version=3,
    research_profile_checked_at=COALESCE(research_profile_checked_at, NOW()),
    updated_at=NOW()
WHERE EXISTS (
    SELECT 1 FROM professor_research_interests i
    WHERE i.professor_id=p.id AND i.evidence_method='MANUAL_REVIEW'
);

UPDATE professors p
SET research_profile_status='NOT_CHECKED',
    research_profile_primary_field=NULL,
    research_profile_source_url=NULL,
    research_profile_confidence=0,
    research_profile_version=0,
    research_profile_checked_at=NULL,
    updated_at=NOW()
WHERE p.research_profile_version < 3
  AND NOT EXISTS (
      SELECT 1 FROM professor_research_interests i
      WHERE i.professor_id=p.id AND i.evidence_method='MANUAL_REVIEW'
  );

CREATE INDEX IF NOT EXISTS professors_research_profile_status_idx
    ON professors (research_profile_status, research_profile_checked_at DESC);

ALTER TABLE roster_member_candidates ADD COLUMN IF NOT EXISTS staff_overrides JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS program_admission_requirements (
    id BIGSERIAL PRIMARY KEY,
    institution_id BIGINT NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
    department_key TEXT NOT NULL DEFAULT '',
    department TEXT,
    degree_type TEXT NOT NULL DEFAULT 'PhD',
    policy TEXT NOT NULL DEFAULT 'NOT_CHECKED',
    minimum_gpa NUMERIC(3, 2),
    evidence_text TEXT,
    source_url TEXT,
    application_cycle TEXT,
    check_status TEXT NOT NULL DEFAULT 'NOT_CHECKED',
    checked_at TIMESTAMPTZ,
    next_check_at TIMESTAMPTZ,
    last_error TEXT,
    UNIQUE (institution_id, department_key, degree_type)
);

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
-- an earlier institution named on that page or matching publication evidence. Existing positive decisions
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
        'A possible official page was found, but the current evidence does not safely connect it to this person.',
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
        CHECK (method IN ('institution_email', 'official_directory', 'institution_admin', 'manual_review')),
    evidence_url TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'verified', 'rejected', 'expired')),
    reviewed_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
    reviewer_notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ
);
ALTER TABLE role_verifications DROP CONSTRAINT IF EXISTS role_verifications_method_check;
ALTER TABLE role_verifications ADD CONSTRAINT role_verifications_method_check
    CHECK (method IN ('institution_email','official_directory','institution_admin','manual_review'));

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
    source_key CHAR(64) NOT NULL UNIQUE,
    title TEXT NOT NULL,
    publication_year INTEGER,
    venue TEXT,
    citation_count INTEGER NOT NULL DEFAULT 0,
    doi TEXT,
    pdf_url TEXT,
    pdf_checked_at TIMESTAMPTZ,
    source_type TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_evidence TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE papers ADD COLUMN IF NOT EXISTS pdf_url TEXT;
ALTER TABLE papers ADD COLUMN IF NOT EXISTS pdf_checked_at TIMESTAMPTZ;
ALTER TABLE papers ADD COLUMN IF NOT EXISTS abstract_text TEXT;
ALTER TABLE papers ADD COLUMN IF NOT EXISTS raw_citation TEXT;
ALTER TABLE papers ADD COLUMN IF NOT EXISTS metadata_status TEXT NOT NULL DEFAULT 'NEEDS_RESOLUTION';
ALTER TABLE papers ADD COLUMN IF NOT EXISTS abstract_status TEXT NOT NULL DEFAULT 'NOT_CHECKED';
ALTER TABLE papers ADD COLUMN IF NOT EXISTS abstract_source_url TEXT;
ALTER TABLE papers ADD COLUMN IF NOT EXISTS abstract_checked_at TIMESTAMPTZ;
ALTER TABLE papers ADD COLUMN IF NOT EXISTS classification_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE papers ADD COLUMN IF NOT EXISTS classified_at TIMESTAMPTZ;
ALTER TABLE papers ADD COLUMN IF NOT EXISTS source_key CHAR(64);
ALTER TABLE papers ADD COLUMN IF NOT EXISTS source_type TEXT;
ALTER TABLE papers ADD COLUMN IF NOT EXISTS source_url TEXT;
ALTER TABLE papers ADD COLUMN IF NOT EXISTS source_evidence TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS papers_source_key_unique_idx
    ON papers (source_key) WHERE source_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS paper_abstract_evidence (
    id BIGSERIAL PRIMARY KEY,
    paper_id BIGINT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    abstract_text TEXT,
    source_url TEXT NOT NULL,
    source_type TEXT NOT NULL,
    title_match_score NUMERIC(5,2) NOT NULL DEFAULT 0,
    doi_match BOOLEAN NOT NULL DEFAULT FALSE,
    verification_status TEXT NOT NULL CHECK (verification_status IN (
        'VERIFIED_DOI','VERIFIED_TITLE','TITLE_CONFLICT','SOURCE_UNAVAILABLE',
        'NOT_FOUND','REVIEW_REQUIRED'
    )),
    content_hash CHAR(64),
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (paper_id, source_url)
);

-- Versioned, explainable research taxonomy and paper/professor assignments.
CREATE TABLE IF NOT EXISTS research_categories (
    id BIGSERIAL PRIMARY KEY,
    category_key TEXT NOT NULL UNIQUE,
    canonical_name TEXT NOT NULL,
    description TEXT NOT NULL,
    parent_key TEXT,
    aliases TEXT[] NOT NULL DEFAULT '{}',
    positive_terms TEXT[] NOT NULL DEFAULT '{}',
    exclusion_terms TEXT[] NOT NULL DEFAULT '{}',
    breadth TEXT NOT NULL DEFAULT 'SPECIALIZED'
        CHECK (breadth IN ('BROAD','SPECIALIZED','NARROW')),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    classification_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS paper_research_categories (
    paper_id BIGINT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    category_id BIGINT NOT NULL REFERENCES research_categories(id) ON DELETE CASCADE,
    lexical_score NUMERIC(5,2) NOT NULL DEFAULT 0,
    concept_score NUMERIC(5,2) NOT NULL DEFAULT 0,
    combined_score NUMERIC(5,2) NOT NULL DEFAULT 0,
    decision TEXT NOT NULL CHECK (decision IN (
        'AUTO_ACCEPTED','AUTO_REJECTED','REVIEW_REQUIRED','QWEN_ACCEPTED','QWEN_REJECTED'
    )),
    evidence_text TEXT,
    matched_terms TEXT[] NOT NULL DEFAULT '{}',
    classification_version INTEGER NOT NULL DEFAULT 1,
    classified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (paper_id, category_id)
);

CREATE TABLE IF NOT EXISTS professor_research_categories (
    professor_id BIGINT NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    category_id BIGINT NOT NULL REFERENCES research_categories(id) ON DELETE CASCADE,
    expertise_score NUMERIC(5,2) NOT NULL DEFAULT 0,
    current_activity_score NUMERIC(5,2) NOT NULL DEFAULT 0,
    matching_paper_count INTEGER NOT NULL DEFAULT 0,
    recent_matching_paper_count INTEGER NOT NULL DEFAULT 0,
    strongest_paper_id BIGINT REFERENCES papers(id) ON DELETE SET NULL,
    latest_matching_year INTEGER,
    status TEXT NOT NULL CHECK (status IN (
        'CURRENTLY_ACTIVE','ESTABLISHED_EXPERTISE','EMERGING_AREA','HISTORICAL_ONLY'
    )),
    classification_version INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (professor_id, category_id)
);
CREATE INDEX IF NOT EXISTS paper_research_categories_current_idx
    ON paper_research_categories (category_id, decision, combined_score DESC);
CREATE INDEX IF NOT EXISTS professor_research_categories_rank_idx
    ON professor_research_categories (category_id, current_activity_score DESC);

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
    review_reason TEXT,
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
ALTER TABLE hiring_signals ADD COLUMN IF NOT EXISTS review_reason TEXT;

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
ALTER TABLE radar_topics
    ADD COLUMN IF NOT EXISTS research_category_id BIGINT
    REFERENCES research_categories(id) ON DELETE SET NULL;

-- Funding freshness is topic- and source-specific. This must be declared
-- after radar_topics so an empty database can apply the schema in one pass.
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
ALTER TABLE radar_topic_professors
    ADD COLUMN IF NOT EXISTS evidence_basis TEXT NOT NULL DEFAULT 'PAPER'
    CHECK (evidence_basis IN ('PAPER','RESEARCH_INTEREST'));
ALTER TABLE radar_topic_professors
    ADD COLUMN IF NOT EXISTS interest_summary TEXT;
ALTER TABLE radar_topic_professors
    ADD COLUMN IF NOT EXISTS interest_source_url TEXT;

-- Exact paper evidence for each topic/professor match. professor_papers is the
-- professor's global publication trail; this table records which paper made a
-- particular professor match a particular radar topic.
CREATE TABLE IF NOT EXISTS radar_topic_professor_papers (
    radar_topic_id BIGINT NOT NULL REFERENCES radar_topics(id) ON DELETE CASCADE,
    professor_id BIGINT NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    paper_id BIGINT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    relevance_score NUMERIC(5, 2) NOT NULL DEFAULT 0,
    matched_query TEXT NOT NULL,
    is_current_match BOOLEAN NOT NULL DEFAULT TRUE,
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_matched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (radar_topic_id, professor_id, paper_id),
    FOREIGN KEY (professor_id, paper_id)
        REFERENCES professor_papers(professor_id, paper_id) ON DELETE CASCADE
);
ALTER TABLE radar_topic_professor_papers
    ADD COLUMN IF NOT EXISTS evidence_method TEXT NOT NULL DEFAULT 'DIRECT_PAPER_TEXT';
ALTER TABLE radar_topic_professor_papers
    ADD COLUMN IF NOT EXISTS matched_text TEXT;

-- Durable work queue. The partial unique index prevents duplicate active work
-- for the same topic/professor while allowing a later refresh job.
CREATE TABLE IF NOT EXISTS radar_jobs (
    id BIGSERIAL PRIMARY KEY,
    institution_id BIGINT REFERENCES institutions(id) ON DELETE CASCADE,
    radar_topic_id BIGINT REFERENCES radar_topics(id) ON DELETE CASCADE,
    professor_id BIGINT REFERENCES professors(id) ON DELETE CASCADE,
    faculty_directory_id BIGINT REFERENCES faculty_directories(id) ON DELETE CASCADE,
    paper_id BIGINT REFERENCES papers(id) ON DELETE CASCADE,
    requested_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
    job_type TEXT NOT NULL CHECK (job_type IN (
        'DISCOVER_FACULTY_DIRECTORIES', 'CRAWL_FACULTY_DIRECTORY',
        'MATCH_FACULTY_PUBLICATIONS', 'QWEN_REVIEW_PUBLICATION', 'QWEN_REVIEW_INTERESTS', 'INDEX_ROSTER_TOPIC',
        'ENRICH_CLASSIFY_PAPER', 'CHECK_HIRING', 'CHECK_GRANTS', 'CHECK_PROGRAM_GPA'
    )),
    dedupe_key TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 50 CHECK (priority BETWEEN 0 AND 100),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    outcome_status TEXT NOT NULL DEFAULT 'PENDING' CHECK (
        outcome_status IN ('PENDING', 'APPROVED', 'REVIEW_REQUIRED', 'REJECTED',
        'NO_CHANGE', 'NO_PUBLICATIONS_FOUND',
        'SOURCE_UNAVAILABLE', 'SUCCEEDED')),
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
ALTER TABLE radar_jobs ADD COLUMN IF NOT EXISTS outcome_status TEXT NOT NULL DEFAULT 'PENDING';
ALTER TABLE radar_jobs DROP CONSTRAINT IF EXISTS radar_jobs_outcome_status_check;
UPDATE radar_jobs SET outcome_status = CASE
    WHEN outcome_status IN ('NO_AUTHOR_MATCH','NO_INDEXED_PUBLICATIONS') THEN 'NO_PUBLICATIONS_FOUND'
    WHEN outcome_status = 'AMBIGUOUS_AUTHOR_MATCH' THEN 'REVIEW_REQUIRED'
    ELSE outcome_status
END;
ALTER TABLE radar_jobs ADD CONSTRAINT radar_jobs_outcome_status_check CHECK (
    outcome_status IN ('PENDING', 'APPROVED', 'REVIEW_REQUIRED', 'REJECTED',
    'NO_CHANGE', 'NO_PUBLICATIONS_FOUND',
    'SOURCE_UNAVAILABLE', 'SUCCEEDED'));
UPDATE radar_jobs
SET outcome_status = CASE
    WHEN status <> 'completed' THEN 'PENDING'
    WHEN job_type='MATCH_FACULTY_PUBLICATIONS' AND result_json->>'status'='NO_PUBLICATIONS_FOUND'
         THEN 'NO_PUBLICATIONS_FOUND'
    WHEN job_type='MATCH_FACULTY_PUBLICATIONS' AND result_json->>'status'='REVIEW_REQUIRED'
         THEN 'REVIEW_REQUIRED'
    WHEN job_type='MATCH_FACULTY_PUBLICATIONS' AND result_json->>'status' IN
         ('OFFICIAL_PUBLICATIONS_FOUND','SCHOLAR_VERIFIED') THEN 'APPROVED'
    WHEN job_type='CRAWL_FACULTY_DIRECTORY'
         AND COALESCE((result_json->>'profiles_pending')::INTEGER,0) > 0
         THEN 'REVIEW_REQUIRED'
    WHEN job_type='CRAWL_FACULTY_DIRECTORY'
         AND COALESCE((result_json->>'profiles_verified')::INTEGER,0) > 0
         THEN 'APPROVED'
    WHEN job_type='DISCOVER_FACULTY_DIRECTORIES'
         AND jsonb_array_length(COALESCE(result_json->'directories','[]'::jsonb)) > 0
         THEN 'APPROVED'
    WHEN job_type='DISCOVER_FACULTY_DIRECTORIES' THEN 'REVIEW_REQUIRED'
    ELSE 'SUCCEEDED'
END
WHERE outcome_status='PENDING';
ALTER TABLE radar_jobs ADD COLUMN IF NOT EXISTS institution_id BIGINT
    REFERENCES institutions(id) ON DELETE CASCADE;
ALTER TABLE radar_jobs ADD COLUMN IF NOT EXISTS paper_id BIGINT
    REFERENCES papers(id) ON DELETE CASCADE;
ALTER TABLE radar_jobs DROP CONSTRAINT IF EXISTS radar_jobs_job_type_check;
ALTER TABLE radar_jobs ADD CONSTRAINT radar_jobs_job_type_check CHECK (job_type IN (
    'DISCOVER_FACULTY_DIRECTORIES', 'CRAWL_FACULTY_DIRECTORY',
    'MATCH_FACULTY_PUBLICATIONS', 'QWEN_REVIEW_PUBLICATION', 'QWEN_REVIEW_INTERESTS', 'INDEX_ROSTER_TOPIC',
    'ENRICH_CLASSIFY_PAPER', 'CHECK_HIRING', 'CHECK_GRANTS', 'CHECK_PROGRAM_GPA'
));

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

-- Columns retained from earlier deployments because provider accounting is
-- still useful to the university-directory recovery search.
ALTER TABLE radar_jobs ADD COLUMN IF NOT EXISTS faculty_directory_id BIGINT
    REFERENCES faculty_directories(id) ON DELETE CASCADE;
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

-- Reconcile canonical provenance from actual current evidence. A legacy label
-- is never enough to call a professor current faculty.
UPDATE professors p
SET data_origin = 'OFFICIAL_DIRECTORY'
WHERE EXISTS (
    SELECT 1
    FROM faculty_directory_memberships membership
    JOIN faculty_directories directory ON directory.id = membership.directory_id
    WHERE membership.professor_id = p.id
      AND membership.currently_listed = TRUE
      AND directory.active = TRUE
);

UPDATE professors p
SET data_origin = 'OFFICIAL_PROFILE'
WHERE data_origin = 'OFFICIAL_DIRECTORY'
  AND NOT EXISTS (
      SELECT 1
      FROM faculty_directory_memberships membership
      JOIN faculty_directories directory ON directory.id = membership.directory_id
      WHERE membership.professor_id = p.id
        AND membership.currently_listed = TRUE
        AND directory.active = TRUE
  )
  AND EXISTS (
      SELECT 1 FROM faculty_verification_evidence evidence
      WHERE evidence.professor_id = p.id
        AND evidence.supports_decision = TRUE
        AND evidence.currentness = 'CURRENT'
        AND evidence.verification_status = 'VERIFIED'
  );

UPDATE professors p
SET faculty_status = 'UNVERIFIED', employment_status = 'UNKNOWN',
    data_origin = 'UNVERIFIED_IMPORT', faculty_confidence = 0,
    next_identity_check_at = NOW(), updated_at = NOW()
WHERE faculty_status = 'VERIFIED'
  AND NOT EXISTS (
      SELECT 1
      FROM faculty_directory_memberships membership
      JOIN faculty_directories directory ON directory.id = membership.directory_id
      WHERE membership.professor_id = p.id
        AND membership.currently_listed = TRUE
        AND directory.active = TRUE
  )
  AND NOT EXISTS (
      SELECT 1 FROM faculty_verification_evidence evidence
      WHERE evidence.professor_id = p.id
        AND evidence.supports_decision = TRUE
        AND evidence.currentness = 'CURRENT'
        AND evidence.verification_status = 'VERIFIED'
  );

-- Latest bounded identity pass: staff-only snippets, page reasons and affiliation trail.
ALTER TABLE professors ADD COLUMN IF NOT EXISTS identity_search_audit JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Apply after the existing schema, with all workers stopped. No data is deleted.
BEGIN;
CREATE TABLE IF NOT EXISTS graduate_programs (
 id BIGSERIAL PRIMARY KEY,
 institution_id BIGINT NOT NULL REFERENCES institutions(id),
 program_name TEXT NOT NULL CHECK (btrim(program_name) <> ''),
 department TEXT,
 degree_type TEXT NOT NULL CHECK (degree_type IN ('MS','MA','PhD','EdD','MBA','Other')),
 verified_at TIMESTAMPTZ,
 UNIQUE(institution_id,program_name,degree_type)
);
CREATE TABLE IF NOT EXISTS professor_graduate_programs (
 professor_id BIGINT NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
 program_id BIGINT NOT NULL REFERENCES graduate_programs(id) ON DELETE CASCADE,
 evidence_url TEXT NOT NULL,
 verified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 PRIMARY KEY(professor_id,program_id)
);
CREATE TABLE IF NOT EXISTS program_admission_sources (
 id BIGSERIAL PRIMARY KEY,
 program_id BIGINT NOT NULL REFERENCES graduate_programs(id) ON DELETE CASCADE,
 source_url TEXT NOT NULL,
 official_domain TEXT NOT NULL,
 requirement_level TEXT NOT NULL CHECK (requirement_level IN ('PROGRAM','GRADUATE_SCHOOL')),
 applicability_verified BOOLEAN NOT NULL DEFAULT FALSE,
 applicability_note TEXT NOT NULL CHECK (btrim(applicability_note) <> ''),
 UNIQUE(program_id,source_url)
);
CREATE TABLE IF NOT EXISTS program_admission_evidence (
 id BIGSERIAL PRIMARY KEY,
 program_id BIGINT NOT NULL REFERENCES graduate_programs(id) ON DELETE CASCADE,
 checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 results_json JSONB NOT NULL
);
ALTER TABLE program_admission_requirements ADD COLUMN IF NOT EXISTS program_id BIGINT REFERENCES graduate_programs(id);
ALTER TABLE program_admission_requirements ALTER COLUMN minimum_gpa TYPE NUMERIC(5,2);
ALTER TABLE program_admission_requirements ADD COLUMN IF NOT EXISTS gpa_scale NUMERIC(5,2);
ALTER TABLE program_admission_requirements ADD COLUMN IF NOT EXISTS gpa_basis TEXT;
ALTER TABLE program_admission_requirements ADD COLUMN IF NOT EXISTS requirement_level TEXT;
ALTER TABLE program_admission_requirements ADD COLUMN IF NOT EXISTS manual_override BOOLEAN NOT NULL DEFAULT FALSE;
CREATE UNIQUE INDEX IF NOT EXISTS program_admission_requirements_program_unique
 ON program_admission_requirements(program_id) WHERE program_id IS NOT NULL;
ALTER TABLE radar_jobs ADD COLUMN IF NOT EXISTS program_id BIGINT REFERENCES graduate_programs(id);
-- Old jobs lack a verified program and degree. Keep them as history, not runnable work.
UPDATE radar_jobs SET status='cancelled',updated_at=NOW(),
 last_error='Legacy GPA job retired: requires a verified graduate program and admissions source.'
 WHERE job_type='CHECK_PROGRAM_GPA' AND program_id IS NULL AND status='queued';
COMMIT;
