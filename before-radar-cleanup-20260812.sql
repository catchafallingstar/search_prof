--
-- PostgreSQL database dump
--

\restrict dbt1ntSL7h8qVkHPrmgO7dmuZMXDdENhZUFvlvDOUiM5GWkdU29tDeQ9hcAzyhQ

-- Dumped from database version 16.14
-- Dumped by pg_dump version 16.14

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: admin_audit_log; Type: TABLE; Schema: public; Owner: scholarradar_app
--

CREATE TABLE public.admin_audit_log (
    id bigint NOT NULL,
    actor_user_id bigint,
    action text NOT NULL,
    target_type text NOT NULL,
    target_id bigint,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.admin_audit_log OWNER TO scholarradar_app;

--
-- Name: admin_audit_log_id_seq; Type: SEQUENCE; Schema: public; Owner: scholarradar_app
--

CREATE SEQUENCE public.admin_audit_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.admin_audit_log_id_seq OWNER TO scholarradar_app;

--
-- Name: admin_audit_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: scholarradar_app
--

ALTER SEQUENCE public.admin_audit_log_id_seq OWNED BY public.admin_audit_log.id;


--
-- Name: fundings; Type: TABLE; Schema: public; Owner: scholarradar_app
--

CREATE TABLE public.fundings (
    id bigint NOT NULL,
    professor_id bigint NOT NULL,
    funding_hash character(64) NOT NULL,
    grant_title text NOT NULL,
    grant_id text,
    funder text NOT NULL,
    amount numeric(14,2),
    award_date date,
    expiration_date date,
    source_url text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.fundings OWNER TO scholarradar_app;

--
-- Name: fundings_id_seq; Type: SEQUENCE; Schema: public; Owner: scholarradar_app
--

CREATE SEQUENCE public.fundings_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.fundings_id_seq OWNER TO scholarradar_app;

--
-- Name: fundings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: scholarradar_app
--

ALTER SEQUENCE public.fundings_id_seq OWNED BY public.fundings.id;


--
-- Name: hiring_signals; Type: TABLE; Schema: public; Owner: scholarradar_app
--

CREATE TABLE public.hiring_signals (
    id bigint NOT NULL,
    professor_id bigint NOT NULL,
    raw_text_hash character(64) NOT NULL,
    signal_type text NOT NULL,
    confidence text NOT NULL,
    raw_text text NOT NULL,
    source_url text NOT NULL,
    observed_at timestamp with time zone DEFAULT now() NOT NULL,
    last_checked_at timestamp with time zone,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT hiring_signals_confidence_check CHECK ((confidence = ANY (ARRAY['low'::text, 'medium'::text, 'high'::text])))
);


ALTER TABLE public.hiring_signals OWNER TO scholarradar_app;

--
-- Name: hiring_signals_id_seq; Type: SEQUENCE; Schema: public; Owner: scholarradar_app
--

CREATE SEQUENCE public.hiring_signals_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.hiring_signals_id_seq OWNER TO scholarradar_app;

--
-- Name: hiring_signals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: scholarradar_app
--

ALTER SEQUENCE public.hiring_signals_id_seq OWNED BY public.hiring_signals.id;


--
-- Name: institution_memberships; Type: TABLE; Schema: public; Owner: scholarradar_app
--

CREATE TABLE public.institution_memberships (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    institution_id bigint NOT NULL,
    title text NOT NULL,
    department text,
    official_profile_url text NOT NULL,
    verification_status text DEFAULT 'pending'::text NOT NULL,
    verified_at timestamp with time zone,
    verification_expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT institution_memberships_verification_status_check CHECK ((verification_status = ANY (ARRAY['pending'::text, 'verified'::text, 'rejected'::text, 'expired'::text])))
);


ALTER TABLE public.institution_memberships OWNER TO scholarradar_app;

--
-- Name: institution_memberships_id_seq; Type: SEQUENCE; Schema: public; Owner: scholarradar_app
--

CREATE SEQUENCE public.institution_memberships_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.institution_memberships_id_seq OWNER TO scholarradar_app;

--
-- Name: institution_memberships_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: scholarradar_app
--

ALTER SEQUENCE public.institution_memberships_id_seq OWNED BY public.institution_memberships.id;


--
-- Name: institutions; Type: TABLE; Schema: public; Owner: scholarradar_app
--

CREATE TABLE public.institutions (
    id bigint NOT NULL,
    name text NOT NULL,
    country_code character(2),
    primary_domain text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.institutions OWNER TO scholarradar_app;

--
-- Name: institutions_id_seq; Type: SEQUENCE; Schema: public; Owner: scholarradar_app
--

CREATE SEQUENCE public.institutions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.institutions_id_seq OWNER TO scholarradar_app;

--
-- Name: institutions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: scholarradar_app
--

ALTER SEQUENCE public.institutions_id_seq OWNED BY public.institutions.id;


--
-- Name: opportunities; Type: TABLE; Schema: public; Owner: scholarradar_app
--

CREATE TABLE public.opportunities (
    id bigint NOT NULL,
    professor_id bigint,
    submitted_by bigint,
    institution_id bigint,
    title text NOT NULL,
    institution_name text NOT NULL,
    professor_name text,
    research_area text NOT NULL,
    position_type text NOT NULL,
    description text NOT NULL,
    funding_status text DEFAULT 'unknown'::text NOT NULL,
    gpa_policy text DEFAULT 'program_minimum'::text NOT NULL,
    international_eligible boolean,
    start_term text,
    application_deadline date,
    application_url text,
    source_kind text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    published_at timestamp with time zone,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT opportunities_funding_status_check CHECK ((funding_status = ANY (ARRAY['confirmed'::text, 'partial'::text, 'unknown'::text]))),
    CONSTRAINT opportunities_gpa_policy_check CHECK ((gpa_policy = ANY (ARRAY['no_lab_cutoff'::text, 'program_minimum'::text, 'exceptions_considered'::text, 'holistic_review'::text, 'not_stated'::text]))),
    CONSTRAINT opportunities_position_type_check CHECK ((position_type = ANY (ARRAY['PhD'::text, 'Postdoc'::text, 'Research Assistant'::text, 'Masters'::text, 'Internship'::text]))),
    CONSTRAINT opportunities_source_kind_check CHECK ((source_kind = ANY (ARRAY['verified_post'::text, 'public_signal'::text, 'university_post'::text]))),
    CONSTRAINT opportunities_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'pending'::text, 'active'::text, 'rejected'::text, 'expired'::text, 'closed'::text])))
);


ALTER TABLE public.opportunities OWNER TO scholarradar_app;

--
-- Name: opportunities_id_seq; Type: SEQUENCE; Schema: public; Owner: scholarradar_app
--

CREATE SEQUENCE public.opportunities_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.opportunities_id_seq OWNER TO scholarradar_app;

--
-- Name: opportunities_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: scholarradar_app
--

ALTER SEQUENCE public.opportunities_id_seq OWNED BY public.opportunities.id;


--
-- Name: opportunity_sources; Type: TABLE; Schema: public; Owner: scholarradar_app
--

CREATE TABLE public.opportunity_sources (
    id bigint NOT NULL,
    opportunity_id bigint NOT NULL,
    source_external_id text,
    source_type text NOT NULL,
    source_url text,
    evidence_text text,
    observed_at timestamp with time zone DEFAULT now() NOT NULL,
    last_checked_at timestamp with time zone,
    confidence text DEFAULT 'medium'::text NOT NULL,
    CONSTRAINT opportunity_sources_confidence_check CHECK ((confidence = ANY (ARRAY['low'::text, 'medium'::text, 'high'::text]))),
    CONSTRAINT opportunity_sources_source_type_check CHECK ((source_type = ANY (ARRAY['professor_attestation'::text, 'university_attestation'::text, 'homepage'::text, 'social'::text, 'grant'::text, 'manual'::text])))
);


ALTER TABLE public.opportunity_sources OWNER TO scholarradar_app;

--
-- Name: opportunity_sources_id_seq; Type: SEQUENCE; Schema: public; Owner: scholarradar_app
--

CREATE SEQUENCE public.opportunity_sources_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.opportunity_sources_id_seq OWNER TO scholarradar_app;

--
-- Name: opportunity_sources_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: scholarradar_app
--

ALTER SEQUENCE public.opportunity_sources_id_seq OWNED BY public.opportunity_sources.id;


--
-- Name: papers; Type: TABLE; Schema: public; Owner: scholarradar_app
--

CREATE TABLE public.papers (
    id bigint NOT NULL,
    openalex_id text NOT NULL,
    title text NOT NULL,
    publication_year integer,
    venue text,
    citation_count integer DEFAULT 0 NOT NULL,
    doi text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.papers OWNER TO scholarradar_app;

--
-- Name: papers_id_seq; Type: SEQUENCE; Schema: public; Owner: scholarradar_app
--

CREATE SEQUENCE public.papers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.papers_id_seq OWNER TO scholarradar_app;

--
-- Name: papers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: scholarradar_app
--

ALTER SEQUENCE public.papers_id_seq OWNED BY public.papers.id;


--
-- Name: professor_papers; Type: TABLE; Schema: public; Owner: scholarradar_app
--

CREATE TABLE public.professor_papers (
    professor_id bigint NOT NULL,
    paper_id bigint NOT NULL,
    author_position text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.professor_papers OWNER TO scholarradar_app;

--
-- Name: professor_profiles; Type: TABLE; Schema: public; Owner: scholarradar_app
--

CREATE TABLE public.professor_profiles (
    id bigint NOT NULL,
    professor_id bigint,
    owner_user_id bigint NOT NULL,
    institution_id bigint,
    title text NOT NULL,
    department text,
    official_profile_url text NOT NULL,
    verification_status text DEFAULT 'pending'::text NOT NULL,
    verified_at timestamp with time zone,
    verification_expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT professor_profiles_verification_status_check CHECK ((verification_status = ANY (ARRAY['pending'::text, 'verified'::text, 'rejected'::text, 'expired'::text])))
);


ALTER TABLE public.professor_profiles OWNER TO scholarradar_app;

--
-- Name: professor_profiles_id_seq; Type: SEQUENCE; Schema: public; Owner: scholarradar_app
--

CREATE SEQUENCE public.professor_profiles_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.professor_profiles_id_seq OWNER TO scholarradar_app;

--
-- Name: professor_profiles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: scholarradar_app
--

ALTER SEQUENCE public.professor_profiles_id_seq OWNED BY public.professor_profiles.id;


--
-- Name: professors; Type: TABLE; Schema: public; Owner: scholarradar_app
--

CREATE TABLE public.professors (
    id bigint NOT NULL,
    openalex_id text,
    name text NOT NULL,
    institution_id bigint,
    institution_name text NOT NULL,
    homepage_url text,
    research_domain text,
    career_stage text DEFAULT 'UNKNOWN'::text NOT NULL,
    radar_score integer DEFAULT 0 NOT NULL,
    score_breakdown text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT professors_career_stage_check CHECK ((career_stage = ANY (ARRAY['NEW_AP'::text, 'ESTABLISHED_PI'::text, 'UNKNOWN'::text])))
);


ALTER TABLE public.professors OWNER TO scholarradar_app;

--
-- Name: professors_id_seq; Type: SEQUENCE; Schema: public; Owner: scholarradar_app
--

CREATE SEQUENCE public.professors_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.professors_id_seq OWNER TO scholarradar_app;

--
-- Name: professors_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: scholarradar_app
--

ALTER SEQUENCE public.professors_id_seq OWNED BY public.professors.id;


--
-- Name: reports; Type: TABLE; Schema: public; Owner: scholarradar_app
--

CREATE TABLE public.reports (
    id bigint NOT NULL,
    opportunity_id bigint NOT NULL,
    reporter_user_id bigint,
    reason text NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT reports_status_check CHECK ((status = ANY (ARRAY['open'::text, 'reviewed'::text, 'dismissed'::text, 'actioned'::text])))
);


ALTER TABLE public.reports OWNER TO scholarradar_app;

--
-- Name: reports_id_seq; Type: SEQUENCE; Schema: public; Owner: scholarradar_app
--

CREATE SEQUENCE public.reports_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.reports_id_seq OWNER TO scholarradar_app;

--
-- Name: reports_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: scholarradar_app
--

ALTER SEQUENCE public.reports_id_seq OWNED BY public.reports.id;


--
-- Name: role_verifications; Type: TABLE; Schema: public; Owner: scholarradar_app
--

CREATE TABLE public.role_verifications (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    professor_profile_id bigint,
    institution_membership_id bigint,
    method text NOT NULL,
    evidence_url text,
    status text DEFAULT 'pending'::text NOT NULL,
    reviewed_by bigint,
    reviewer_notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    reviewed_at timestamp with time zone,
    CONSTRAINT role_verifications_method_check CHECK ((method = ANY (ARRAY['institution_email'::text, 'official_directory'::text, 'orcid'::text, 'institution_admin'::text, 'manual_review'::text]))),
    CONSTRAINT role_verifications_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'verified'::text, 'rejected'::text, 'expired'::text])))
);


ALTER TABLE public.role_verifications OWNER TO scholarradar_app;

--
-- Name: role_verifications_id_seq; Type: SEQUENCE; Schema: public; Owner: scholarradar_app
--

CREATE SEQUENCE public.role_verifications_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.role_verifications_id_seq OWNER TO scholarradar_app;

--
-- Name: role_verifications_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: scholarradar_app
--

ALTER SEQUENCE public.role_verifications_id_seq OWNED BY public.role_verifications.id;


--
-- Name: site_admins; Type: TABLE; Schema: public; Owner: scholarradar_app
--

CREATE TABLE public.site_admins (
    user_id bigint NOT NULL,
    admin_role text NOT NULL,
    granted_by bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_at timestamp with time zone,
    CONSTRAINT site_admins_admin_role_check CHECK ((admin_role = ANY (ARRAY['owner'::text, 'moderator'::text])))
);


ALTER TABLE public.site_admins OWNER TO scholarradar_app;

--
-- Name: sponsorships; Type: TABLE; Schema: public; Owner: scholarradar_app
--

CREATE TABLE public.sponsorships (
    id bigint NOT NULL,
    opportunity_id bigint NOT NULL,
    sponsor_user_id bigint,
    starts_at timestamp with time zone NOT NULL,
    ends_at timestamp with time zone NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT sponsorships_check CHECK ((ends_at > starts_at)),
    CONSTRAINT sponsorships_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'active'::text, 'ended'::text, 'refunded'::text])))
);


ALTER TABLE public.sponsorships OWNER TO scholarradar_app;

--
-- Name: sponsorships_id_seq; Type: SEQUENCE; Schema: public; Owner: scholarradar_app
--

CREATE SEQUENCE public.sponsorships_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.sponsorships_id_seq OWNER TO scholarradar_app;

--
-- Name: sponsorships_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: scholarradar_app
--

ALTER SEQUENCE public.sponsorships_id_seq OWNED BY public.sponsorships.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: scholarradar_app
--

CREATE TABLE public.users (
    id bigint NOT NULL,
    oidc_subject text NOT NULL,
    email text NOT NULL,
    display_name text NOT NULL,
    account_role text DEFAULT 'applicant'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT users_account_role_check CHECK ((account_role = ANY (ARRAY['applicant'::text, 'professor'::text, 'institution_admin'::text])))
);


ALTER TABLE public.users OWNER TO scholarradar_app;

--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: scholarradar_app
--

CREATE SEQUENCE public.users_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.users_id_seq OWNER TO scholarradar_app;

--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: scholarradar_app
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: admin_audit_log id; Type: DEFAULT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.admin_audit_log ALTER COLUMN id SET DEFAULT nextval('public.admin_audit_log_id_seq'::regclass);


--
-- Name: fundings id; Type: DEFAULT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.fundings ALTER COLUMN id SET DEFAULT nextval('public.fundings_id_seq'::regclass);


--
-- Name: hiring_signals id; Type: DEFAULT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.hiring_signals ALTER COLUMN id SET DEFAULT nextval('public.hiring_signals_id_seq'::regclass);


--
-- Name: institution_memberships id; Type: DEFAULT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.institution_memberships ALTER COLUMN id SET DEFAULT nextval('public.institution_memberships_id_seq'::regclass);


--
-- Name: institutions id; Type: DEFAULT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.institutions ALTER COLUMN id SET DEFAULT nextval('public.institutions_id_seq'::regclass);


--
-- Name: opportunities id; Type: DEFAULT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.opportunities ALTER COLUMN id SET DEFAULT nextval('public.opportunities_id_seq'::regclass);


--
-- Name: opportunity_sources id; Type: DEFAULT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.opportunity_sources ALTER COLUMN id SET DEFAULT nextval('public.opportunity_sources_id_seq'::regclass);


--
-- Name: papers id; Type: DEFAULT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.papers ALTER COLUMN id SET DEFAULT nextval('public.papers_id_seq'::regclass);


--
-- Name: professor_profiles id; Type: DEFAULT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.professor_profiles ALTER COLUMN id SET DEFAULT nextval('public.professor_profiles_id_seq'::regclass);


--
-- Name: professors id; Type: DEFAULT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.professors ALTER COLUMN id SET DEFAULT nextval('public.professors_id_seq'::regclass);


--
-- Name: reports id; Type: DEFAULT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.reports ALTER COLUMN id SET DEFAULT nextval('public.reports_id_seq'::regclass);


--
-- Name: role_verifications id; Type: DEFAULT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.role_verifications ALTER COLUMN id SET DEFAULT nextval('public.role_verifications_id_seq'::regclass);


--
-- Name: sponsorships id; Type: DEFAULT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.sponsorships ALTER COLUMN id SET DEFAULT nextval('public.sponsorships_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Data for Name: admin_audit_log; Type: TABLE DATA; Schema: public; Owner: scholarradar_app
--

COPY public.admin_audit_log (id, actor_user_id, action, target_type, target_id, notes, created_at) FROM stdin;
1	1	bootstrap_owner	user	1	Initial owner bootstrap	2026-08-12 16:15:37.958981+00
2	1	reject	profile	1	not matching	2026-08-12 16:59:10.026937+00
\.


--
-- Data for Name: fundings; Type: TABLE DATA; Schema: public; Owner: scholarradar_app
--

COPY public.fundings (id, professor_id, funding_hash, grant_title, grant_id, funder, amount, award_date, expiration_date, source_url, created_at) FROM stdin;
1	3	6ceed119fd43130df42f947b7ec2b3e06fcb3fb399c30cb9a5d47e88b15e1173	IUSE/PFE:RED A&I: Professional Formation of Chemical Engineers through Cross-curricular Team-based Hands-on and Web-based Interactive Experiences	2550943	NSF	0.00	2026-09-01	2031-08-31	https://www.nsf.gov/awardsearch/showAward?AWD_ID=2550943	2026-08-12 17:02:31.990433+00
2	3	0859d2ee8379961a1ed0b2ed6ad9b9d6eabae84305a3266f07be67172d7ac98b	Collaborative Research: Comparing Effectiveness of Web-based Interactive Digital Experiments to Physical Experiments for Engineering Classrooms	2336988	NSF	0.00	2024-07-01	2027-06-30	https://www.nsf.gov/awardsearch/showAward?AWD_ID=2336988	2026-08-12 17:02:31.990433+00
3	4	c2fef6238caa49e72245ec7a8bf9ce11eaf1c6d8d653d8ffca7d8d62895890ef	The Berkeley Data Science Education Fellowship: Exploring Ethical and Inclusive Approaches to Data Science in a Shifting Landscape	2430522	NSF	0.00	2024-10-01	2027-09-30	https://www.nsf.gov/awardsearch/showAward?AWD_ID=2430522	2026-08-12 17:02:31.990433+00
4	4	f0c5292621ff42914faf3a4d8ac44e977f14943e0e7a5607f065f8702090084c	Developing inclusive, interdisciplinary undergraduate data science curricula in  computing and social science	2245877	NSF	0.00	2023-06-01	2027-05-31	https://www.nsf.gov/awardsearch/showAward?AWD_ID=2245877	2026-08-12 17:02:31.990433+00
13	15	9af7da9ede1919aeb7a353e774ccbe56f9db4482c1bdac680267165c75587883	Collaborative Research: III: Small: A DREAM Proactive Conversational System	2336769	NSF	268047.00	2024-06-01	2027-05-31	https://www.nsf.gov/awardsearch/showAward?AWD_ID=2336769	2026-08-12 18:09:27.374475+00
14	18	739e8270da5d044e2788fb5bb3f1693b5a41ef951496879e5732622995059ba7	Collaborative Research: SaTC: CORE: Medium: Towards Secure Federated Learning	2131938	NSF	300000.00	2022-10-01	2026-09-30	https://www.nsf.gov/awardsearch/showAward?AWD_ID=2131938	2026-08-12 18:09:27.374475+00
15	22	2178ddc538fe3512af6144e9e4665125f0b6895118d399ac97bf82f9140ba5b6	Mcity 2: An Integrated Automated Testbed for Autonomous Transportation Research	2223517	NSF	5118625.00	2022-10-01	2026-09-30	https://www.nsf.gov/awardsearch/showAward?AWD_ID=2223517	2026-08-12 18:09:27.374475+00
\.


--
-- Data for Name: hiring_signals; Type: TABLE DATA; Schema: public; Owner: scholarradar_app
--

COPY public.hiring_signals (id, professor_id, raw_text_hash, signal_type, confidence, raw_text, source_url, observed_at, last_checked_at, expires_at, created_at) FROM stdin;
\.


--
-- Data for Name: institution_memberships; Type: TABLE DATA; Schema: public; Owner: scholarradar_app
--

COPY public.institution_memberships (id, user_id, institution_id, title, department, official_profile_url, verification_status, verified_at, verification_expires_at, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: institutions; Type: TABLE DATA; Schema: public; Owner: scholarradar_app
--

COPY public.institutions (id, name, country_code, primary_domain, created_at) FROM stdin;
1	Umich	\N	\N	2026-08-12 16:58:30.934054+00
2	Northern Arizona University	US	\N	2026-08-12 17:02:31.950723+00
3	Grand Valley State University	US	\N	2026-08-12 17:02:31.950723+00
4	Washington State University	US	\N	2026-08-12 17:02:31.950723+00
5	University of California, Berkeley	US	\N	2026-08-12 17:02:31.950723+00
6	The University of Texas at San Antonio	US	\N	2026-08-12 17:02:31.950723+00
8	Ohio University	US	\N	2026-08-12 17:02:31.950723+00
10	Coastal Bend College	US	\N	2026-08-12 17:02:31.950723+00
7	Auburn University	US	\N	2026-08-12 17:02:31.950723+00
40	Duke University	US	\N	2026-08-12 18:09:27.3333+00
41	Texas A&M University – Central Texas	US	\N	2026-08-12 18:09:27.3333+00
42	Purdue University West Lafayette	US	\N	2026-08-12 18:09:27.3333+00
43	University of Michigan	US	\N	2026-08-12 18:09:27.3333+00
32	Kennesaw State University	US	\N	2026-08-12 18:09:27.3333+00
33	Florida International University	US	\N	2026-08-12 18:09:27.3333+00
9	Massachusetts Institute of Technology	US	\N	2026-08-12 17:02:31.950723+00
35	University of Colorado Colorado Springs	US	\N	2026-08-12 18:09:27.3333+00
36	University of Washington	US	\N	2026-08-12 18:09:27.3333+00
37	New Mexico State University	US	\N	2026-08-12 18:09:27.3333+00
38	Icahn School of Medicine at Mount Sinai	US	\N	2026-08-12 18:09:27.3333+00
39	Princeton University	US	\N	2026-08-12 18:09:27.3333+00
\.


--
-- Data for Name: opportunities; Type: TABLE DATA; Schema: public; Owner: scholarradar_app
--

COPY public.opportunities (id, professor_id, submitted_by, institution_id, title, institution_name, professor_name, research_area, position_type, description, funding_status, gpa_policy, international_eligible, start_term, application_deadline, application_url, source_kind, status, published_at, expires_at, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: opportunity_sources; Type: TABLE DATA; Schema: public; Owner: scholarradar_app
--

COPY public.opportunity_sources (id, opportunity_id, source_external_id, source_type, source_url, evidence_text, observed_at, last_checked_at, confidence) FROM stdin;
\.


--
-- Data for Name: papers; Type: TABLE DATA; Schema: public; Owner: scholarradar_app
--

COPY public.papers (id, openalex_id, title, publication_year, venue, citation_count, doi, created_at) FROM stdin;
40	https://openalex.org/W4402483954	Toward Enhancing Privacy Preservation of a Federated Learning CNN Intrusion Detection System in IoT: Method and Empirical Study	2024	ACM Transactions on Software Engineering and Methodology	83	https://doi.org/10.1145/3695998	2026-08-12 18:09:27.3333+00
41	https://openalex.org/W4402774698	Preserving Fairness Generalization in Deepfake Detection	2024		83	https://doi.org/10.1109/cvpr52733.2024.01591	2026-08-12 18:09:27.3333+00
42	https://openalex.org/W4399368914	Curse of rarity for autonomous vehicles	2024	Nature Communications	81	https://doi.org/10.1038/s41467-024-49194-0	2026-08-12 18:09:27.3333+00
31	https://openalex.org/W4400076000	A Robust Privacy-Preserving Federated Learning Model Against Model Poisoning Attacks	2024	IEEE Transactions on Information Forensics and Security	340	https://doi.org/10.1109/tifs.2024.3420126	2026-08-12 18:09:27.3333+00
32	https://openalex.org/W4406302454	Security and Privacy Challenges of Large Language Models: A Survey	2025	ACM Computing Surveys	287	https://doi.org/10.1145/3712001	2026-08-12 18:09:27.3333+00
33	https://openalex.org/W4396796749	AI deception: A survey of examples, risks, and potential solutions	2024	Patterns	197	https://doi.org/10.1016/j.patter.2024.100988	2026-08-12 18:09:27.3333+00
34	https://openalex.org/W4399919514	Explainable artificial intelligence: A survey of needs, techniques, applications, and future direction	2024	Neurocomputing	192	https://doi.org/10.1016/j.neucom.2024.128111	2026-08-12 18:09:27.3333+00
35	https://openalex.org/W4400461591	Counterfactual Explanations and Algorithmic Recourses for Machine Learning: A Review	2024	ACM Computing Surveys	171	https://doi.org/10.1145/3677119	2026-08-12 18:09:27.3333+00
36	https://openalex.org/W4391399751	Human-in-the-Loop Reinforcement Learning: A Survey and Position on Requirements, Challenges, and Opportunities	2024	Journal of Artificial Intelligence Research	143	https://doi.org/10.1613/jair.1.15348	2026-08-12 18:09:27.3333+00
37	https://openalex.org/W4412853478	Multi-model assurance analysis showing large language models are highly vulnerable to adversarial hallucination attacks during clinical decision support	2025	Communications Medicine	127	https://doi.org/10.1038/s43856-025-01021-3	2026-08-12 18:09:27.3333+00
1	https://openalex.org/W4396647972	Application and Challenges of Computer Networks in Distance Education	2024	Computing Performance and Communication systems	25	https://doi.org/10.23977/cpcs.2024.080103	2026-08-12 17:02:31.950723+00
2	https://openalex.org/W4404884248	Exploring Key Drivers for Embracing Artificial Intelligence in Public Relations Pedagogy	2024	Journalism & Mass Communication Educator	7	https://doi.org/10.1177/10776958241299075	2026-08-12 17:02:31.950723+00
3	https://openalex.org/W3191691398	Progress in the Nationwide Dissemination and Assessment of Low-Cost Desktop Learning Modules and Adaptation of Pedagogy to a Virtual Era	2024	2021 ASEE Virtual Annual Conference Content Access Proceedings	4	https://doi.org/10.18260/1-2--37606	2026-08-12 17:02:31.950723+00
4	https://openalex.org/W4407681453	Exploration of Undergraduate Teaching Assistant Identity and Teaching Goals in Data Science Courses	2025		3	https://doi.org/10.1145/3641555.3705179	2026-08-12 17:02:31.950723+00
5	https://openalex.org/W4413822184	AI Fusion of Vision and Management for 6G Millimeter-Wave Aerial Networks	2025	IEEE Access	2	https://doi.org/10.1109/access.2025.3604288	2026-08-12 17:02:31.950723+00
6	https://openalex.org/W4402597248	Scientometric, Thematic, and Methodological Analysis of IJCER Construction Education Focused Publications – 2004 - 2023	2024	International Journal of Construction Education and Research	1	https://doi.org/10.1080/15578771.2024.2404019	2026-08-12 17:02:31.950723+00
7	https://openalex.org/W4396908156	Creating VR Content for Training Purposes	2024	International Journal of Technology in Education and Science	2	https://doi.org/10.46328/ijtes.550	2026-08-12 17:02:31.950723+00
8	https://openalex.org/W4401287190	A Comparative Study of the Impact of Virtual Reality on Student Learning and Satisfaction in Aerospace Education	2024		2	https://doi.org/10.18260/1-2--46426	2026-08-12 17:02:31.950723+00
9	https://openalex.org/W4400889107	Meta-Analysis of the Effectiveness of MyMathLab in College Algebra (Poster 34)	2024		1	https://doi.org/10.3102/ip.24.2107354	2026-08-12 17:02:31.950723+00
10	https://openalex.org/W4411834677	Focusing on Improved Construction Quality through Augmented Reality Training	2025		1	https://doi.org/10.4995/head25.2025.20121	2026-08-12 17:02:31.950723+00
39	https://openalex.org/W4393034172	Trustworthy Graph Neural Networks: Aspects, Methods, and Trends	2024	Proceedings of the IEEE	105	https://doi.org/10.1109/jproc.2024.3369017	2026-08-12 18:09:27.3333+00
38	https://openalex.org/W4393157467	Visual Adversarial Examples Jailbreak Aligned Large Language Models	2024	Proceedings of the AAAI Conference on Artificial Intelligence	110	https://doi.org/10.1609/aaai.v38i19.30150	2026-08-12 18:09:27.3333+00
\.


--
-- Data for Name: professor_papers; Type: TABLE DATA; Schema: public; Owner: scholarradar_app
--

COPY public.professor_papers (professor_id, paper_id, author_position, created_at) FROM stdin;
1	1	first	2026-08-12 17:02:31.950723+00
2	2	last	2026-08-12 17:02:31.950723+00
3	3	last	2026-08-12 17:02:31.950723+00
4	4	last	2026-08-12 17:02:31.950723+00
5	5	last	2026-08-12 17:02:31.950723+00
6	6	first	2026-08-12 17:02:31.950723+00
7	7	last	2026-08-12 17:02:31.950723+00
8	8	last	2026-08-12 17:02:31.950723+00
9	9	first	2026-08-12 17:02:31.950723+00
10	10	last	2026-08-12 17:02:31.950723+00
11	31	last	2026-08-12 18:09:27.3333+00
12	32	last	2026-08-12 18:09:27.3333+00
13	33	first	2026-08-12 18:09:27.3333+00
14	34	first	2026-08-12 18:09:27.3333+00
15	35	last	2026-08-12 18:09:27.3333+00
16	36	last	2026-08-12 18:09:27.3333+00
17	37	first	2026-08-12 18:09:27.3333+00
18	38	last	2026-08-12 18:09:27.3333+00
19	39	last	2026-08-12 18:09:27.3333+00
20	40	last	2026-08-12 18:09:27.3333+00
21	41	last	2026-08-12 18:09:27.3333+00
22	42	first	2026-08-12 18:09:27.3333+00
\.


--
-- Data for Name: professor_profiles; Type: TABLE DATA; Schema: public; Owner: scholarradar_app
--

COPY public.professor_profiles (id, professor_id, owner_user_id, institution_id, title, department, official_profile_url, verification_status, verified_at, verification_expires_at, created_at, updated_at) FROM stdin;
1	\N	1	1	AI security	dod	https://www.linkedin.com/in/mingtian-tan-a94ab4339/?locale=en	rejected	\N	\N	2026-08-12 16:58:30.934054+00	2026-08-12 16:59:10.026937+00
\.


--
-- Data for Name: professors; Type: TABLE DATA; Schema: public; Owner: scholarradar_app
--

COPY public.professors (id, openalex_id, name, institution_id, institution_name, homepage_url, research_domain, career_stage, radar_score, score_breakdown, created_at, updated_at) FROM stdin;
19	https://openalex.org/A5062247330	Jian Pei	40	Duke University	\N	Adversarial Robustness in Machine Learning	UNKNOWN	0		2026-08-12 18:09:27.3333+00	2026-08-12 18:09:27.3333+00
20	https://openalex.org/A5107160502	Brandon Sabrsula	41	Texas A&M University – Central Texas	\N	Adversarial Robustness in Machine Learning	UNKNOWN	0		2026-08-12 18:09:27.3333+00	2026-08-12 18:09:27.3333+00
21	https://openalex.org/A5100687829	Shu Hu	42	Purdue University West Lafayette	\N	Adversarial Robustness in Machine Learning	UNKNOWN	0		2026-08-12 18:09:27.3333+00	2026-08-12 18:09:27.3333+00
22	https://openalex.org/A5083695605	Henry Liu	43	University of Michigan	\N	Adversarial Robustness in Machine Learning	UNKNOWN	15	 +15 (active NSF awards)	2026-08-12 18:09:27.3333+00	2026-08-12 18:09:27.374475+00
1	https://openalex.org/A5103442224	Ying Lin	2	Northern Arizona University	\N	Innovative Educational Techniques	UNKNOWN	0		2026-08-12 17:02:31.950723+00	2026-08-12 17:30:44.577413+00
2	https://openalex.org/A5113094633	Seung woo Choi	3	Grand Valley State University	\N	Innovative Educational Techniques	UNKNOWN	0		2026-08-12 17:02:31.950723+00	2026-08-12 17:30:44.577413+00
3	https://openalex.org/A5007745379	David B. Thiessen	4	Washington State University	\N	Innovative Educational Techniques	UNKNOWN	30	 +30 (active NSF awards)	2026-08-12 17:02:31.950723+00	2026-08-12 17:30:44.577413+00
4	https://openalex.org/A5037393859	Lisa Yan	5	University of California, Berkeley	\N	Innovative Educational Techniques	UNKNOWN	30	 +30 (active NSF awards)	2026-08-12 17:02:31.950723+00	2026-08-12 17:30:44.577413+00
5	https://openalex.org/A5024204464	Brian Kelley	6	The University of Texas at San Antonio	\N	Innovative Educational Techniques	UNKNOWN	0		2026-08-12 17:02:31.950723+00	2026-08-12 17:30:44.577413+00
6	https://openalex.org/A5024576241	Wesley Collins	7	Auburn University	\N	Innovative Educational Techniques	UNKNOWN	0		2026-08-12 17:02:31.950723+00	2026-08-12 17:30:44.577413+00
7	https://openalex.org/A5087232575	Adonis Durado	8	Ohio University	\N	Innovative Educational Techniques	UNKNOWN	0		2026-08-12 17:02:31.950723+00	2026-08-12 17:30:44.577413+00
8	https://openalex.org/A5082750695	Yun Chang	9	Massachusetts Institute of Technology	\N	Innovative Educational Techniques	UNKNOWN	0		2026-08-12 17:02:31.950723+00	2026-08-12 17:30:44.577413+00
9	https://openalex.org/A5111198777	Michael Wang	10	Coastal Bend College	\N	Innovative Educational Techniques	UNKNOWN	0		2026-08-12 17:02:31.950723+00	2026-08-12 17:30:44.577413+00
10	https://openalex.org/A5007063773	Darren Olsen	7	Auburn University	\N	Innovative Educational Techniques	UNKNOWN	0		2026-08-12 17:02:31.950723+00	2026-08-12 17:30:44.577413+00
11	https://openalex.org/A5087589295	Reza M. Parizi	32	Kennesaw State University	\N	Adversarial Robustness in Machine Learning	UNKNOWN	0		2026-08-12 18:09:27.3333+00	2026-08-12 18:09:35.245915+00
12	https://openalex.org/A5060093535	Yanzhao Wu	33	Florida International University	\N	Adversarial Robustness in Machine Learning	UNKNOWN	0		2026-08-12 18:09:27.3333+00	2026-08-12 18:09:35.245915+00
13	https://openalex.org/A5087342854	Peter S. Park	9	Massachusetts Institute of Technology	\N	Adversarial Robustness in Machine Learning	UNKNOWN	0		2026-08-12 18:09:27.3333+00	2026-08-12 18:09:35.245915+00
14	https://openalex.org/A5093470856	Melkamu Abay Mersha	35	University of Colorado Colorado Springs	\N	Adversarial Robustness in Machine Learning	UNKNOWN	0		2026-08-12 18:09:27.3333+00	2026-08-12 18:09:35.245915+00
15	https://openalex.org/A5061319881	Chirag Shah	36	University of Washington	\N	Adversarial Robustness in Machine Learning	UNKNOWN	15	 +15 (active NSF awards)	2026-08-12 18:09:27.3333+00	2026-08-12 18:09:35.245915+00
16	https://openalex.org/A5034657358	Andreas Holzinger	37	New Mexico State University	\N	Adversarial Robustness in Machine Learning	UNKNOWN	0		2026-08-12 18:09:27.3333+00	2026-08-12 18:09:35.245915+00
17	https://openalex.org/A5044457381	Mahmud Omar	38	Icahn School of Medicine at Mount Sinai	\N	Adversarial Robustness in Machine Learning	UNKNOWN	0		2026-08-12 18:09:27.3333+00	2026-08-12 18:09:35.245915+00
18	https://openalex.org/A5015619835	Prateek Mittal	39	Princeton University	\N	Adversarial Robustness in Machine Learning	UNKNOWN	15	 +15 (active NSF awards)	2026-08-12 18:09:27.3333+00	2026-08-12 18:09:35.245915+00
\.


--
-- Data for Name: reports; Type: TABLE DATA; Schema: public; Owner: scholarradar_app
--

COPY public.reports (id, opportunity_id, reporter_user_id, reason, status, created_at) FROM stdin;
\.


--
-- Data for Name: role_verifications; Type: TABLE DATA; Schema: public; Owner: scholarradar_app
--

COPY public.role_verifications (id, user_id, professor_profile_id, institution_membership_id, method, evidence_url, status, reviewed_by, reviewer_notes, created_at, reviewed_at) FROM stdin;
1	1	1	\N	official_directory	https://www.linkedin.com/in/mingtian-tan-a94ab4339/?locale=en	rejected	1	not matching	2026-08-12 16:58:30.934054+00	2026-08-12 16:59:10.026937+00
\.


--
-- Data for Name: site_admins; Type: TABLE DATA; Schema: public; Owner: scholarradar_app
--

COPY public.site_admins (user_id, admin_role, granted_by, created_at, revoked_at) FROM stdin;
1	owner	\N	2026-08-12 16:15:37.958981+00	\N
\.


--
-- Data for Name: sponsorships; Type: TABLE DATA; Schema: public; Owner: scholarradar_app
--

COPY public.sponsorships (id, opportunity_id, sponsor_user_id, starts_at, ends_at, status, created_at) FROM stdin;
\.


--
-- Data for Name: users; Type: TABLE DATA; Schema: public; Owner: scholarradar_app
--

COPY public.users (id, oidc_subject, email, display_name, account_role, created_at, updated_at) FROM stdin;
27	local-dev:replace_with_your_email@example.com	replace_with_your_email@example.com	Replace With Your Name	applicant	2026-08-12 17:51:55.380532+00	2026-08-12 17:51:55.694637+00
1	local-dev:2023melodyb@gmail.com	2023melodyb@gmail.com	Local Site Owner	applicant	2026-08-12 16:15:37.937765+00	2026-08-12 18:09:08.614638+00
\.


--
-- Name: admin_audit_log_id_seq; Type: SEQUENCE SET; Schema: public; Owner: scholarradar_app
--

SELECT pg_catalog.setval('public.admin_audit_log_id_seq', 2, true);


--
-- Name: fundings_id_seq; Type: SEQUENCE SET; Schema: public; Owner: scholarradar_app
--

SELECT pg_catalog.setval('public.fundings_id_seq', 18, true);


--
-- Name: hiring_signals_id_seq; Type: SEQUENCE SET; Schema: public; Owner: scholarradar_app
--

SELECT pg_catalog.setval('public.hiring_signals_id_seq', 1, false);


--
-- Name: institution_memberships_id_seq; Type: SEQUENCE SET; Schema: public; Owner: scholarradar_app
--

SELECT pg_catalog.setval('public.institution_memberships_id_seq', 1, false);


--
-- Name: institutions_id_seq; Type: SEQUENCE SET; Schema: public; Owner: scholarradar_app
--

SELECT pg_catalog.setval('public.institutions_id_seq', 51, true);


--
-- Name: opportunities_id_seq; Type: SEQUENCE SET; Schema: public; Owner: scholarradar_app
--

SELECT pg_catalog.setval('public.opportunities_id_seq', 1, false);


--
-- Name: opportunity_sources_id_seq; Type: SEQUENCE SET; Schema: public; Owner: scholarradar_app
--

SELECT pg_catalog.setval('public.opportunity_sources_id_seq', 1, false);


--
-- Name: papers_id_seq; Type: SEQUENCE SET; Schema: public; Owner: scholarradar_app
--

SELECT pg_catalog.setval('public.papers_id_seq', 50, true);


--
-- Name: professor_profiles_id_seq; Type: SEQUENCE SET; Schema: public; Owner: scholarradar_app
--

SELECT pg_catalog.setval('public.professor_profiles_id_seq', 1, true);


--
-- Name: professors_id_seq; Type: SEQUENCE SET; Schema: public; Owner: scholarradar_app
--

SELECT pg_catalog.setval('public.professors_id_seq', 22, true);


--
-- Name: reports_id_seq; Type: SEQUENCE SET; Schema: public; Owner: scholarradar_app
--

SELECT pg_catalog.setval('public.reports_id_seq', 1, false);


--
-- Name: role_verifications_id_seq; Type: SEQUENCE SET; Schema: public; Owner: scholarradar_app
--

SELECT pg_catalog.setval('public.role_verifications_id_seq', 1, true);


--
-- Name: sponsorships_id_seq; Type: SEQUENCE SET; Schema: public; Owner: scholarradar_app
--

SELECT pg_catalog.setval('public.sponsorships_id_seq', 1, false);


--
-- Name: users_id_seq; Type: SEQUENCE SET; Schema: public; Owner: scholarradar_app
--

SELECT pg_catalog.setval('public.users_id_seq', 32, true);


--
-- Name: admin_audit_log admin_audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.admin_audit_log
    ADD CONSTRAINT admin_audit_log_pkey PRIMARY KEY (id);


--
-- Name: fundings fundings_funding_hash_key; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.fundings
    ADD CONSTRAINT fundings_funding_hash_key UNIQUE (funding_hash);


--
-- Name: fundings fundings_pkey; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.fundings
    ADD CONSTRAINT fundings_pkey PRIMARY KEY (id);


--
-- Name: hiring_signals hiring_signals_pkey; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.hiring_signals
    ADD CONSTRAINT hiring_signals_pkey PRIMARY KEY (id);


--
-- Name: hiring_signals hiring_signals_raw_text_hash_key; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.hiring_signals
    ADD CONSTRAINT hiring_signals_raw_text_hash_key UNIQUE (raw_text_hash);


--
-- Name: institution_memberships institution_memberships_pkey; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.institution_memberships
    ADD CONSTRAINT institution_memberships_pkey PRIMARY KEY (id);


--
-- Name: institution_memberships institution_memberships_user_unique; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.institution_memberships
    ADD CONSTRAINT institution_memberships_user_unique UNIQUE (user_id);


--
-- Name: institutions institutions_name_unique; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.institutions
    ADD CONSTRAINT institutions_name_unique UNIQUE (name);


--
-- Name: institutions institutions_pkey; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.institutions
    ADD CONSTRAINT institutions_pkey PRIMARY KEY (id);


--
-- Name: opportunities opportunities_pkey; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.opportunities
    ADD CONSTRAINT opportunities_pkey PRIMARY KEY (id);


--
-- Name: opportunity_sources opportunity_sources_pkey; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.opportunity_sources
    ADD CONSTRAINT opportunity_sources_pkey PRIMARY KEY (id);


--
-- Name: opportunity_sources opportunity_sources_source_external_id_key; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.opportunity_sources
    ADD CONSTRAINT opportunity_sources_source_external_id_key UNIQUE (source_external_id);


--
-- Name: papers papers_openalex_id_key; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.papers
    ADD CONSTRAINT papers_openalex_id_key UNIQUE (openalex_id);


--
-- Name: papers papers_pkey; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.papers
    ADD CONSTRAINT papers_pkey PRIMARY KEY (id);


--
-- Name: professor_papers professor_papers_pkey; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.professor_papers
    ADD CONSTRAINT professor_papers_pkey PRIMARY KEY (professor_id, paper_id);


--
-- Name: professor_profiles professor_profiles_owner_unique; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.professor_profiles
    ADD CONSTRAINT professor_profiles_owner_unique UNIQUE (owner_user_id);


--
-- Name: professor_profiles professor_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.professor_profiles
    ADD CONSTRAINT professor_profiles_pkey PRIMARY KEY (id);


--
-- Name: professor_profiles professor_profiles_professor_unique; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.professor_profiles
    ADD CONSTRAINT professor_profiles_professor_unique UNIQUE (professor_id);


--
-- Name: professors professors_name_institution_unique; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.professors
    ADD CONSTRAINT professors_name_institution_unique UNIQUE (name, institution_name);


--
-- Name: professors professors_openalex_unique; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.professors
    ADD CONSTRAINT professors_openalex_unique UNIQUE (openalex_id);


--
-- Name: professors professors_pkey; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.professors
    ADD CONSTRAINT professors_pkey PRIMARY KEY (id);


--
-- Name: reports reports_pkey; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.reports
    ADD CONSTRAINT reports_pkey PRIMARY KEY (id);


--
-- Name: role_verifications role_verifications_pkey; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.role_verifications
    ADD CONSTRAINT role_verifications_pkey PRIMARY KEY (id);


--
-- Name: site_admins site_admins_pkey; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.site_admins
    ADD CONSTRAINT site_admins_pkey PRIMARY KEY (user_id);


--
-- Name: sponsorships sponsorships_pkey; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.sponsorships
    ADD CONSTRAINT sponsorships_pkey PRIMARY KEY (id);


--
-- Name: users users_email_unique; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_unique UNIQUE (email);


--
-- Name: users users_oidc_subject_key; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_oidc_subject_key UNIQUE (oidc_subject);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: admin_audit_log_created_idx; Type: INDEX; Schema: public; Owner: scholarradar_app
--

CREATE INDEX admin_audit_log_created_idx ON public.admin_audit_log USING btree (created_at DESC);


--
-- Name: hiring_signals_professor_idx; Type: INDEX; Schema: public; Owner: scholarradar_app
--

CREATE INDEX hiring_signals_professor_idx ON public.hiring_signals USING btree (professor_id);


--
-- Name: institution_memberships_status_idx; Type: INDEX; Schema: public; Owner: scholarradar_app
--

CREATE INDEX institution_memberships_status_idx ON public.institution_memberships USING btree (verification_status);


--
-- Name: opportunities_active_search_idx; Type: INDEX; Schema: public; Owner: scholarradar_app
--

CREATE INDEX opportunities_active_search_idx ON public.opportunities USING btree (status, position_type, research_area, application_deadline);


--
-- Name: opportunities_institution_idx; Type: INDEX; Schema: public; Owner: scholarradar_app
--

CREATE INDEX opportunities_institution_idx ON public.opportunities USING btree (institution_name);


--
-- Name: professor_profiles_status_idx; Type: INDEX; Schema: public; Owner: scholarradar_app
--

CREATE INDEX professor_profiles_status_idx ON public.professor_profiles USING btree (verification_status);


--
-- Name: professors_domain_idx; Type: INDEX; Schema: public; Owner: scholarradar_app
--

CREATE INDEX professors_domain_idx ON public.professors USING btree (research_domain);


--
-- Name: professors_score_idx; Type: INDEX; Schema: public; Owner: scholarradar_app
--

CREATE INDEX professors_score_idx ON public.professors USING btree (radar_score DESC);


--
-- Name: role_verifications_status_idx; Type: INDEX; Schema: public; Owner: scholarradar_app
--

CREATE INDEX role_verifications_status_idx ON public.role_verifications USING btree (status);


--
-- Name: site_admins_active_idx; Type: INDEX; Schema: public; Owner: scholarradar_app
--

CREATE INDEX site_admins_active_idx ON public.site_admins USING btree (admin_role) WHERE (revoked_at IS NULL);


--
-- Name: site_admins_one_active_owner_idx; Type: INDEX; Schema: public; Owner: scholarradar_app
--

CREATE UNIQUE INDEX site_admins_one_active_owner_idx ON public.site_admins USING btree (admin_role) WHERE ((admin_role = 'owner'::text) AND (revoked_at IS NULL));


--
-- Name: admin_audit_log admin_audit_log_actor_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.admin_audit_log
    ADD CONSTRAINT admin_audit_log_actor_user_id_fkey FOREIGN KEY (actor_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: fundings fundings_professor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.fundings
    ADD CONSTRAINT fundings_professor_id_fkey FOREIGN KEY (professor_id) REFERENCES public.professors(id) ON DELETE CASCADE;


--
-- Name: hiring_signals hiring_signals_professor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.hiring_signals
    ADD CONSTRAINT hiring_signals_professor_id_fkey FOREIGN KEY (professor_id) REFERENCES public.professors(id) ON DELETE CASCADE;


--
-- Name: institution_memberships institution_memberships_institution_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.institution_memberships
    ADD CONSTRAINT institution_memberships_institution_id_fkey FOREIGN KEY (institution_id) REFERENCES public.institutions(id) ON DELETE CASCADE;


--
-- Name: institution_memberships institution_memberships_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.institution_memberships
    ADD CONSTRAINT institution_memberships_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: opportunities opportunities_institution_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.opportunities
    ADD CONSTRAINT opportunities_institution_id_fkey FOREIGN KEY (institution_id) REFERENCES public.institutions(id) ON DELETE SET NULL;


--
-- Name: opportunities opportunities_professor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.opportunities
    ADD CONSTRAINT opportunities_professor_id_fkey FOREIGN KEY (professor_id) REFERENCES public.professors(id) ON DELETE SET NULL;


--
-- Name: opportunities opportunities_submitted_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.opportunities
    ADD CONSTRAINT opportunities_submitted_by_fkey FOREIGN KEY (submitted_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: opportunity_sources opportunity_sources_opportunity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.opportunity_sources
    ADD CONSTRAINT opportunity_sources_opportunity_id_fkey FOREIGN KEY (opportunity_id) REFERENCES public.opportunities(id) ON DELETE CASCADE;


--
-- Name: professor_papers professor_papers_paper_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.professor_papers
    ADD CONSTRAINT professor_papers_paper_id_fkey FOREIGN KEY (paper_id) REFERENCES public.papers(id) ON DELETE CASCADE;


--
-- Name: professor_papers professor_papers_professor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.professor_papers
    ADD CONSTRAINT professor_papers_professor_id_fkey FOREIGN KEY (professor_id) REFERENCES public.professors(id) ON DELETE CASCADE;


--
-- Name: professor_profiles professor_profiles_institution_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.professor_profiles
    ADD CONSTRAINT professor_profiles_institution_id_fkey FOREIGN KEY (institution_id) REFERENCES public.institutions(id) ON DELETE SET NULL;


--
-- Name: professor_profiles professor_profiles_owner_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.professor_profiles
    ADD CONSTRAINT professor_profiles_owner_user_id_fkey FOREIGN KEY (owner_user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: professor_profiles professor_profiles_professor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.professor_profiles
    ADD CONSTRAINT professor_profiles_professor_id_fkey FOREIGN KEY (professor_id) REFERENCES public.professors(id) ON DELETE SET NULL;


--
-- Name: professors professors_institution_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.professors
    ADD CONSTRAINT professors_institution_id_fkey FOREIGN KEY (institution_id) REFERENCES public.institutions(id) ON DELETE SET NULL;


--
-- Name: reports reports_opportunity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.reports
    ADD CONSTRAINT reports_opportunity_id_fkey FOREIGN KEY (opportunity_id) REFERENCES public.opportunities(id) ON DELETE CASCADE;


--
-- Name: reports reports_reporter_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.reports
    ADD CONSTRAINT reports_reporter_user_id_fkey FOREIGN KEY (reporter_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: role_verifications role_verifications_institution_membership_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.role_verifications
    ADD CONSTRAINT role_verifications_institution_membership_id_fkey FOREIGN KEY (institution_membership_id) REFERENCES public.institution_memberships(id) ON DELETE CASCADE;


--
-- Name: role_verifications role_verifications_professor_profile_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.role_verifications
    ADD CONSTRAINT role_verifications_professor_profile_id_fkey FOREIGN KEY (professor_profile_id) REFERENCES public.professor_profiles(id) ON DELETE CASCADE;


--
-- Name: role_verifications role_verifications_reviewed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.role_verifications
    ADD CONSTRAINT role_verifications_reviewed_by_fkey FOREIGN KEY (reviewed_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: role_verifications role_verifications_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.role_verifications
    ADD CONSTRAINT role_verifications_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: site_admins site_admins_granted_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.site_admins
    ADD CONSTRAINT site_admins_granted_by_fkey FOREIGN KEY (granted_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: site_admins site_admins_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.site_admins
    ADD CONSTRAINT site_admins_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: sponsorships sponsorships_opportunity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.sponsorships
    ADD CONSTRAINT sponsorships_opportunity_id_fkey FOREIGN KEY (opportunity_id) REFERENCES public.opportunities(id) ON DELETE CASCADE;


--
-- Name: sponsorships sponsorships_sponsor_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: scholarradar_app
--

ALTER TABLE ONLY public.sponsorships
    ADD CONSTRAINT sponsorships_sponsor_user_id_fkey FOREIGN KEY (sponsor_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- PostgreSQL database dump complete
--

\unrestrict dbt1ntSL7h8qVkHPrmgO7dmuZMXDdENhZUFvlvDOUiM5GWkdU29tDeQ9hcAzyhQ

