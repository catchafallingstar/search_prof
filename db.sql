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
    radar_score INTEGER NOT NULL DEFAULT 0,
    score_breakdown TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT professors_openalex_unique UNIQUE (openalex_id),
    CONSTRAINT professors_name_institution_unique UNIQUE (name, institution_name)
);

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
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('draft', 'pending', 'active', 'rejected', 'expired', 'closed')),
    published_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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
