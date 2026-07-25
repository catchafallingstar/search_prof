-- PostgreSQL Database Schema for ScholarRadar

-- 1. Professors Table
CREATE TABLE IF NOT EXISTS professors (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    institution VARCHAR(255),
    homepage_url TEXT,
    research_domain VARCHAR(255),
    hiring_score INT DEFAULT 0,
    career_stage VARCHAR(50) DEFAULT 'ESTABLISHED_PI', -- Options: 'NEW_AP', 'ESTABLISHED_PI', 'UNKNOWN'
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_prof_inst UNIQUE (name, institution)
);

-- 2. Papers Table
CREATE TABLE IF NOT EXISTS papers (
    id SERIAL PRIMARY KEY,
    openalex_id VARCHAR(255) UNIQUE,
    title TEXT NOT NULL,
    publication_year INT,
    venue TEXT,
    citation_count INT DEFAULT 0,
    doi TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 3. Professor-Papers Junction Table
CREATE TABLE IF NOT EXISTS professor_papers (
    id SERIAL PRIMARY KEY,
    professor_id INT REFERENCES professors(id) ON DELETE CASCADE,
    paper_id INT REFERENCES papers(id) ON DELETE CASCADE,
    author_position VARCHAR(50), -- e.g., 'first', 'last', 'corresponding'
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_prof_paper UNIQUE (professor_id, paper_id)
);

-- 4. Fundings / Grants Table
CREATE TABLE IF NOT EXISTS fundings (
    id SERIAL PRIMARY KEY,
    professor_id INT REFERENCES professors(id) ON DELETE CASCADE,
    grant_title TEXT,
    grant_id VARCHAR(255),
    funder VARCHAR(255) DEFAULT 'NSF', -- e.g., 'NSF CRII', 'NSF CAREER'
    amount NUMERIC(12, 2),
    award_date DATE,
    raw_text TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 5. Hiring Signals Table
CREATE TABLE IF NOT EXISTS hiring_signals (
    id SERIAL PRIMARY KEY,
    professor_id INT REFERENCES professors(id) ON DELETE CASCADE,
    signal_type VARCHAR(100), -- e.g., 'NSF_GRANT', 'POSTDOC_TRANSITION', 'WEB_ANNOUNCEMENT'
    confidence_score VARCHAR(50),
    raw_text TEXT,
    source_url TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_professors_domain ON professors(research_domain);
CREATE INDEX IF NOT EXISTS idx_professors_score ON professors(hiring_score);
CREATE INDEX IF NOT EXISTS idx_signals_prof_id ON hiring_signals(professor_id);