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
        CHECK (faculty_status IN ('UNVERIFIED', 'VERIFIED', 'NOT_FACULTY', 'CONFLICT', 'MANUAL_REVIEW')),
    faculty_title TEXT,
    faculty_source_url TEXT,
    faculty_verification_method TEXT,
    faculty_verification_version INTEGER NOT NULL DEFAULT 0,
    faculty_confidence NUMERIC(4, 3) NOT NULL DEFAULT 0
        CHECK (faculty_confidence BETWEEN 0 AND 1),
    faculty_checked_at TIMESTAMPTZ,
    faculty_verified_at TIMESTAMPTZ,
    public_hiring_checked_at TIMESTAMPTZ,
    grant_checked_at TIMESTAMPTZ,
    official_institution_domain TEXT,
    appointment_year INTEGER CHECK (appointment_year IS NULL OR appointment_year BETWEEN 1900 AND 2200),
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
ALTER TABLE professors ADD COLUMN IF NOT EXISTS public_hiring_checked_at TIMESTAMPTZ;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS grant_checked_at TIMESTAMPTZ;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS official_institution_domain TEXT;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS appointment_year INTEGER;
ALTER TABLE professors ADD COLUMN IF NOT EXISTS graduate_faculty_status TEXT NOT NULL DEFAULT 'UNKNOWN';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'professors_faculty_status_check') THEN
        ALTER TABLE professors ADD CONSTRAINT professors_faculty_status_check
            CHECK (faculty_status IN ('UNVERIFIED', 'VERIFIED', 'NOT_FACULTY', 'CONFLICT', 'MANUAL_REVIEW'));
    END IF;
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

CREATE TABLE IF NOT EXISTS faculty_verification_evidence (
    id BIGSERIAL PRIMARY KEY,
    professor_id BIGINT NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    source_domain TEXT NOT NULL,
    observed_title TEXT,
    observed_institution TEXT,
    evidence_text TEXT,
    verification_status TEXT NOT NULL
        CHECK (verification_status IN ('UNVERIFIED', 'VERIFIED', 'NOT_FACULTY', 'CONFLICT', 'MANUAL_REVIEW')),
    confidence NUMERIC(4, 3) NOT NULL DEFAULT 0
        CHECK (confidence BETWEEN 0 AND 1),
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT faculty_verification_evidence_unique UNIQUE (professor_id, source_url)
);

CREATE INDEX IF NOT EXISTS faculty_verification_evidence_professor_idx
    ON faculty_verification_evidence (professor_id, checked_at DESC);

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
    faculty_verification_version = 2,
    faculty_confidence = 1.0,
    faculty_checked_at = COALESCE(pp.verified_at, NOW()),
    faculty_verified_at = COALESCE(pp.verified_at, NOW()),
    homepage_url = COALESCE(p.homepage_url, pp.official_profile_url),
    updated_at = NOW()
FROM professor_profiles pp
WHERE pp.professor_id = p.id
  AND pp.verification_status = 'verified';

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
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS professor_papers (
    professor_id BIGINT NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    paper_id BIGINT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    author_position TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (professor_id, paper_id)
);

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
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_checked_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
        CHECK (status IN ('running', 'completed', 'failed', 'cancelled')),
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
