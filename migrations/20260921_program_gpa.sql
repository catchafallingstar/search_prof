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
