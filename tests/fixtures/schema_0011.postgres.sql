CREATE EXTENSION IF NOT EXISTS vector;
CREATE OR REPLACE FUNCTION public.quality_audit_append_only()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
          BEGIN RAISE EXCEPTION 'audit history is append-only'; END; $function$

--
-- PostgreSQL database dump
--

-- Dumped from database version 16.2
-- Dumped by pg_dump version 16.2

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
-- Name: adapters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.adapters (
    name character varying(200) NOT NULL,
    protocol character varying(32) NOT NULL,
    config json NOT NULL,
    health json NOT NULL,
    enabled boolean NOT NULL,
    provenance character varying(24) NOT NULL,
    source_registry json NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.adapters FORCE ROW LEVEL SECURITY;


--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: approvals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.approvals (
    case_id character varying(64) NOT NULL,
    plan_id character varying(64) NOT NULL,
    actor_id character varying(64) NOT NULL,
    approved boolean NOT NULL,
    reason text NOT NULL,
    fingerprint character varying(64) NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.approvals FORCE ROW LEVEL SECURITY;


--
-- Name: audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit (
    actor_id character varying(100) NOT NULL,
    action character varying(100) NOT NULL,
    target_id character varying(100) NOT NULL,
    request_id character varying(120) NOT NULL,
    detail json NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.audit FORCE ROW LEVEL SECURITY;


--
-- Name: cases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cases (
    title character varying(200) NOT NULL,
    line_id character varying(64) NOT NULL,
    process character varying(40) NOT NULL,
    status character varying(40) NOT NULL,
    version integer NOT NULL,
    author_id character varying(64) NOT NULL,
    context json NOT NULL,
    diagnosis json NOT NULL,
    rounds integer NOT NULL,
    model_calls integer NOT NULL,
    outcome json NOT NULL,
    review_flags json NOT NULL,
    anomaly_key character varying(120),
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.cases FORCE ROW LEVEL SECURITY;


--
-- Name: commands; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.commands (
    key character varying(120) NOT NULL,
    actor_id character varying(100) NOT NULL,
    fingerprint character varying(64) NOT NULL,
    response json NOT NULL,
    authorization_hash character varying(64) NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.commands FORCE ROW LEVEL SECURITY;


--
-- Name: dispositions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dispositions (
    object_id character varying(120) NOT NULL,
    route_id character varying(64) NOT NULL,
    case_id character varying(64),
    action character varying(20) NOT NULL,
    status character varying(30) NOT NULL,
    author_id character varying(64) NOT NULL,
    reviewer_id character varying(64),
    reason text NOT NULL,
    cycle integer NOT NULL,
    return_step_id character varying(64),
    version integer NOT NULL,
    data json NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.dispositions FORCE ROW LEVEL SECURITY;


--
-- Name: distributions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.distributions (
    target_node character varying(100) NOT NULL,
    author_id character varying(64) NOT NULL,
    kind character varying(30) NOT NULL,
    record_id character varying(64) NOT NULL,
    action character varying(20) NOT NULL,
    generation integer NOT NULL,
    status character varying(30) NOT NULL,
    package json NOT NULL,
    result json NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.distributions FORCE ROW LEVEL SECURITY;


--
-- Name: evidence; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.evidence (
    filename character varying(200) NOT NULL,
    object_key character varying(500) NOT NULL,
    sha256 character varying(64) NOT NULL,
    media_type character varying(100) NOT NULL,
    size integer NOT NULL,
    source character varying(32) NOT NULL,
    status character varying(32) NOT NULL,
    replication_status character varying(24) NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.evidence FORCE ROW LEVEL SECURITY;


--
-- Name: inbox; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.inbox (
    event_id character varying(64) NOT NULL,
    source_node character varying(100) NOT NULL,
    sequence integer NOT NULL,
    payload json NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.inbox FORCE ROW LEVEL SECURITY;


--
-- Name: inspections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.inspections (
    source_id character varying(120) NOT NULL,
    record_id character varying(120) NOT NULL,
    line_id character varying(64) NOT NULL,
    object_id character varying(120),
    granularity character varying(30) NOT NULL,
    association character varying(40) NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    status character varying(40) NOT NULL,
    provenance character varying(24) NOT NULL,
    result json NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.inspections FORCE ROW LEVEL SECURITY;


--
-- Name: knowledge; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.knowledge (
    category character varying(30) NOT NULL,
    title character varying(200) NOT NULL,
    content text NOT NULL,
    status character varying(32) NOT NULL,
    revision integer NOT NULL,
    author_id character varying(64) NOT NULL,
    reviewer_id character varying(64),
    scope json NOT NULL,
    source json NOT NULL,
    rules json NOT NULL,
    previous_id character varying(64),
    valid_until timestamp with time zone NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.knowledge FORCE ROW LEVEL SECURITY;


--
-- Name: knowledge_vectors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.knowledge_vectors (
    knowledge_id character varying(64) NOT NULL,
    embedding public.vector(384) NOT NULL,
    model_version character varying(100) NOT NULL,
    content_hash character varying(64) NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.knowledge_vectors FORCE ROW LEVEL SECURITY;


--
-- Name: lab_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lab_results (
    object_id character varying(120) NOT NULL,
    metric character varying(100) NOT NULL,
    method character varying(200) NOT NULL,
    value character varying(100) NOT NULL,
    unit character varying(30) NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    operator_id character varying(64) NOT NULL,
    evidence_ids json NOT NULL,
    data json NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.lab_results FORCE ROW LEVEL SECURITY;


--
-- Name: learning_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.learning_records (
    kind character varying(30) NOT NULL,
    title character varying(200) NOT NULL,
    status character varying(32) NOT NULL,
    version integer NOT NULL,
    revision integer NOT NULL,
    author_id character varying(64) NOT NULL,
    reviewer_id character varying(64),
    case_id character varying(64),
    file_id character varying(64),
    previous_id character varying(64),
    knowledge_id character varying(64),
    payload json NOT NULL,
    snapshot json NOT NULL,
    activation json NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.learning_records FORCE ROW LEVEL SECURITY;


--
-- Name: learning_tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.learning_tasks (
    record_id character varying(64) NOT NULL,
    kind character varying(30) NOT NULL,
    actor_id character varying(64) NOT NULL,
    authorization_hash character varying(64) NOT NULL,
    status character varying(30) NOT NULL,
    attempts integer NOT NULL,
    lease_token character varying(64) NOT NULL,
    lease_until timestamp with time zone,
    next_at timestamp with time zone NOT NULL,
    data json NOT NULL,
    result json NOT NULL,
    error_code character varying(100) NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.learning_tasks FORCE ROW LEVEL SECURITY;


--
-- Name: masters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.masters (
    kind character varying(40) NOT NULL,
    code character varying(120) NOT NULL,
    name character varying(200) NOT NULL,
    parent_id character varying(64),
    data json NOT NULL,
    version integer NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.masters FORCE ROW LEVEL SECURITY;


--
-- Name: measurements; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.measurements (
    source_id character varying(100) NOT NULL,
    record_id character varying(120) NOT NULL,
    equipment_id character varying(120) NOT NULL,
    metric character varying(100) NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    value double precision NOT NULL,
    unit character varying(40) NOT NULL,
    quality character varying(40) NOT NULL,
    value_type character varying(16) NOT NULL,
    data json NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.measurements FORCE ROW LEVEL SECURITY;


--
-- Name: outbox; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.outbox (
    kind character varying(100) NOT NULL,
    payload json NOT NULL,
    status character varying(30) NOT NULL,
    attempts integer NOT NULL,
    next_at timestamp with time zone NOT NULL,
    last_error text NOT NULL,
    sequence integer NOT NULL,
    synced boolean NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.outbox FORCE ROW LEVEL SECURITY;


--
-- Name: passages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.passages (
    object_id character varying(120) NOT NULL,
    granularity character varying(30) NOT NULL,
    route_id character varying(64) NOT NULL,
    step_id character varying(64) NOT NULL,
    sequence integer NOT NULL,
    entered_at timestamp with time zone NOT NULL,
    exited_at timestamp with time zone NOT NULL,
    data json NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.passages FORCE ROW LEVEL SECURITY;


--
-- Name: plans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.plans (
    case_id character varying(64) NOT NULL,
    revision integer NOT NULL,
    author_id character varying(64) NOT NULL,
    status character varying(32) NOT NULL,
    content json NOT NULL,
    fingerprint character varying(64) NOT NULL,
    context_hash character varying(64) NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.plans FORCE ROW LEVEL SECURITY;


--
-- Name: raw_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.raw_records (
    source_id character varying(100) NOT NULL,
    record_id character varying(160) NOT NULL,
    source_session_id character varying(100) NOT NULL,
    payload_hash character varying(64) NOT NULL,
    object_key character varying(500) NOT NULL,
    mapping_version character varying(100) NOT NULL,
    status character varying(32) NOT NULL,
    source_time timestamp with time zone,
    received_at timestamp with time zone NOT NULL,
    normalized_ids json NOT NULL,
    errors json NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.raw_records FORCE ROW LEVEL SECURITY;


--
-- Name: receipts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.receipts (
    case_id character varying(64) NOT NULL,
    plan_id character varying(64) NOT NULL,
    actor_id character varying(64) NOT NULL,
    content json NOT NULL,
    effective_at timestamp with time zone NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.receipts FORCE ROW LEVEL SECURITY;


--
-- Name: remote_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.remote_requests (
    author_id character varying(64) NOT NULL,
    target_node character varying(100) NOT NULL,
    kind character varying(40) NOT NULL,
    payload json NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    status character varying(30) NOT NULL,
    result json NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.remote_requests FORCE ROW LEVEL SECURITY;


--
-- Name: retests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.retests (
    case_id character varying(64) NOT NULL,
    plan_id character varying(64) NOT NULL,
    actor_id character varying(64) NOT NULL,
    result json NOT NULL,
    verdict character varying(30) NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.retests FORCE ROW LEVEL SECURITY;


--
-- Name: routes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.routes (
    name character varying(200) NOT NULL,
    process character varying(40) NOT NULL,
    status character varying(30) NOT NULL,
    revision integer NOT NULL,
    steps json NOT NULL,
    disposition json NOT NULL,
    author_id character varying(64) NOT NULL,
    previous_id character varying(64),
    reviewer_id character varying(64),
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.routes FORCE ROW LEVEL SECURITY;


--
-- Name: runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.runs (
    case_id character varying(64) NOT NULL,
    status character varying(32) NOT NULL,
    data json NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.runs FORCE ROW LEVEL SECURITY;


--
-- Name: sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sessions (
    user_id character varying(64) NOT NULL,
    token_hash character varying(64) NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    auth_revision integer NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.sessions FORCE ROW LEVEL SECURITY;


--
-- Name: sites; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sites (
    node_id character varying(100) NOT NULL,
    name character varying(160) NOT NULL,
    token_hash character varying(64) NOT NULL,
    active boolean NOT NULL,
    last_seen timestamp with time zone,
    sequence integer NOT NULL,
    acknowledged integer NOT NULL,
    data json NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.sites FORCE ROW LEVEL SECURITY;


--
-- Name: snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.snapshots (
    source_node character varying(100) NOT NULL,
    entity_id character varying(64) NOT NULL,
    version integer NOT NULL,
    data json NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.snapshots FORCE ROW LEVEL SECURITY;


--
-- Name: study_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.study_groups (
    dataset_id character varying(64) NOT NULL,
    group_key character varying(160) NOT NULL,
    split character varying(30) NOT NULL,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.study_groups FORCE ROW LEVEL SECURITY;


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    username character varying(120) NOT NULL,
    password_hash text NOT NULL,
    role character varying(32) NOT NULL,
    allowed_sites json NOT NULL,
    allowed_lines json NOT NULL,
    active boolean NOT NULL,
    auth_revision integer NOT NULL,
    auth_origin character varying(30) NOT NULL,
    center_revision integer NOT NULL,
    central_active boolean NOT NULL,
    lease_expires_at timestamp with time zone,
    id character varying(64) NOT NULL,
    tenant_id character varying(80) NOT NULL,
    site_id character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    scope_line_id character varying(100) NOT NULL
);

ALTER TABLE ONLY public.users FORCE ROW LEVEL SECURITY;


--
-- Name: adapters adapters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.adapters
    ADD CONSTRAINT adapters_pkey PRIMARY KEY (id);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: approvals approvals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT approvals_pkey PRIMARY KEY (id);


--
-- Name: approvals approvals_plan_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT approvals_plan_id_key UNIQUE (plan_id);


--
-- Name: audit audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit
    ADD CONSTRAINT audit_pkey PRIMARY KEY (id);


--
-- Name: cases cases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cases
    ADD CONSTRAINT cases_pkey PRIMARY KEY (id);


--
-- Name: commands commands_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commands
    ADD CONSTRAINT commands_pkey PRIMARY KEY (id);


--
-- Name: commands commands_tenant_id_site_id_actor_id_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commands
    ADD CONSTRAINT commands_tenant_id_site_id_actor_id_key_key UNIQUE (tenant_id, site_id, actor_id, key);


--
-- Name: dispositions dispositions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dispositions
    ADD CONSTRAINT dispositions_pkey PRIMARY KEY (id);


--
-- Name: distributions distributions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.distributions
    ADD CONSTRAINT distributions_pkey PRIMARY KEY (id);


--
-- Name: distributions distributions_tenant_id_target_node_kind_record_id_generati_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.distributions
    ADD CONSTRAINT distributions_tenant_id_target_node_kind_record_id_generati_key UNIQUE (tenant_id, target_node, kind, record_id, generation);


--
-- Name: evidence evidence_object_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_object_key_key UNIQUE (object_key);


--
-- Name: evidence evidence_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_pkey PRIMARY KEY (id);


--
-- Name: inbox inbox_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inbox
    ADD CONSTRAINT inbox_pkey PRIMARY KEY (id);


--
-- Name: inbox inbox_tenant_id_source_node_event_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inbox
    ADD CONSTRAINT inbox_tenant_id_source_node_event_id_key UNIQUE (tenant_id, source_node, event_id);


--
-- Name: inspections inspections_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inspections
    ADD CONSTRAINT inspections_pkey PRIMARY KEY (id);


--
-- Name: inspections inspections_tenant_id_site_id_source_id_record_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inspections
    ADD CONSTRAINT inspections_tenant_id_site_id_source_id_record_id_key UNIQUE (tenant_id, site_id, source_id, record_id);


--
-- Name: knowledge knowledge_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge
    ADD CONSTRAINT knowledge_pkey PRIMARY KEY (id);


--
-- Name: knowledge_vectors knowledge_vectors_knowledge_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_vectors
    ADD CONSTRAINT knowledge_vectors_knowledge_id_key UNIQUE (knowledge_id);


--
-- Name: knowledge_vectors knowledge_vectors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_vectors
    ADD CONSTRAINT knowledge_vectors_pkey PRIMARY KEY (id);


--
-- Name: lab_results lab_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lab_results
    ADD CONSTRAINT lab_results_pkey PRIMARY KEY (id);


--
-- Name: learning_records learning_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.learning_records
    ADD CONSTRAINT learning_records_pkey PRIMARY KEY (id);


--
-- Name: learning_tasks learning_tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.learning_tasks
    ADD CONSTRAINT learning_tasks_pkey PRIMARY KEY (id);


--
-- Name: masters masters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.masters
    ADD CONSTRAINT masters_pkey PRIMARY KEY (id);


--
-- Name: masters masters_tenant_id_site_id_kind_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.masters
    ADD CONSTRAINT masters_tenant_id_site_id_kind_code_key UNIQUE (tenant_id, site_id, kind, code);


--
-- Name: measurements measurements_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.measurements
    ADD CONSTRAINT measurements_pkey PRIMARY KEY (id);


--
-- Name: measurements measurements_tenant_id_site_id_source_id_record_id_metric_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.measurements
    ADD CONSTRAINT measurements_tenant_id_site_id_source_id_record_id_metric_key UNIQUE (tenant_id, site_id, source_id, record_id, metric);


--
-- Name: outbox outbox_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbox
    ADD CONSTRAINT outbox_pkey PRIMARY KEY (id);


--
-- Name: passages passages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.passages
    ADD CONSTRAINT passages_pkey PRIMARY KEY (id);


--
-- Name: passages passages_tenant_id_site_id_object_id_sequence_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.passages
    ADD CONSTRAINT passages_tenant_id_site_id_object_id_sequence_key UNIQUE (tenant_id, site_id, object_id, sequence);


--
-- Name: plans plans_case_id_revision_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plans
    ADD CONSTRAINT plans_case_id_revision_key UNIQUE (case_id, revision);


--
-- Name: plans plans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plans
    ADD CONSTRAINT plans_pkey PRIMARY KEY (id);


--
-- Name: raw_records raw_records_object_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_records
    ADD CONSTRAINT raw_records_object_key_key UNIQUE (object_key);


--
-- Name: raw_records raw_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_records
    ADD CONSTRAINT raw_records_pkey PRIMARY KEY (id);


--
-- Name: raw_records raw_records_tenant_id_site_id_source_id_source_session_id_r_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_records
    ADD CONSTRAINT raw_records_tenant_id_site_id_source_id_source_session_id_r_key UNIQUE (tenant_id, site_id, source_id, source_session_id, record_id, payload_hash);


--
-- Name: receipts receipts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.receipts
    ADD CONSTRAINT receipts_pkey PRIMARY KEY (id);


--
-- Name: receipts receipts_plan_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.receipts
    ADD CONSTRAINT receipts_plan_id_key UNIQUE (plan_id);


--
-- Name: remote_requests remote_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.remote_requests
    ADD CONSTRAINT remote_requests_pkey PRIMARY KEY (id);


--
-- Name: retests retests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.retests
    ADD CONSTRAINT retests_pkey PRIMARY KEY (id);


--
-- Name: routes routes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routes
    ADD CONSTRAINT routes_pkey PRIMARY KEY (id);


--
-- Name: runs runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT runs_pkey PRIMARY KEY (id);


--
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (id);


--
-- Name: sessions sessions_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_token_hash_key UNIQUE (token_hash);


--
-- Name: sites sites_node_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sites
    ADD CONSTRAINT sites_node_id_key UNIQUE (node_id);


--
-- Name: sites sites_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sites
    ADD CONSTRAINT sites_pkey PRIMARY KEY (id);


--
-- Name: snapshots snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.snapshots
    ADD CONSTRAINT snapshots_pkey PRIMARY KEY (id);


--
-- Name: snapshots snapshots_tenant_id_source_node_entity_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.snapshots
    ADD CONSTRAINT snapshots_tenant_id_source_node_entity_id_key UNIQUE (tenant_id, source_node, entity_id);


--
-- Name: study_groups study_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.study_groups
    ADD CONSTRAINT study_groups_pkey PRIMARY KEY (id);


--
-- Name: study_groups study_groups_tenant_id_site_id_group_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.study_groups
    ADD CONSTRAINT study_groups_tenant_id_site_id_group_key_key UNIQUE (tenant_id, site_id, group_key);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_tenant_id_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_tenant_id_username_key UNIQUE (tenant_id, username);


--
-- Name: ix_adapters_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_adapters_scope_line_id ON public.adapters USING btree (scope_line_id);


--
-- Name: ix_adapters_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_adapters_site_id ON public.adapters USING btree (site_id);


--
-- Name: ix_adapters_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_adapters_tenant_id ON public.adapters USING btree (tenant_id);


--
-- Name: ix_approvals_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_approvals_scope_line_id ON public.approvals USING btree (scope_line_id);


--
-- Name: ix_approvals_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_approvals_site_id ON public.approvals USING btree (site_id);


--
-- Name: ix_approvals_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_approvals_tenant_id ON public.approvals USING btree (tenant_id);


--
-- Name: ix_audit_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_action ON public.audit USING btree (action);


--
-- Name: ix_audit_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_scope_line_id ON public.audit USING btree (scope_line_id);


--
-- Name: ix_audit_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_site_id ON public.audit USING btree (site_id);


--
-- Name: ix_audit_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_tenant_id ON public.audit USING btree (tenant_id);


--
-- Name: ix_case_anomaly; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_case_anomaly ON public.cases USING btree (tenant_id, site_id, anomaly_key);


--
-- Name: ix_cases_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cases_line_id ON public.cases USING btree (line_id);


--
-- Name: ix_cases_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cases_scope_line_id ON public.cases USING btree (scope_line_id);


--
-- Name: ix_cases_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cases_site_id ON public.cases USING btree (site_id);


--
-- Name: ix_cases_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cases_status ON public.cases USING btree (status);


--
-- Name: ix_cases_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cases_tenant_id ON public.cases USING btree (tenant_id);


--
-- Name: ix_commands_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_commands_scope_line_id ON public.commands USING btree (scope_line_id);


--
-- Name: ix_commands_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_commands_site_id ON public.commands USING btree (site_id);


--
-- Name: ix_commands_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_commands_tenant_id ON public.commands USING btree (tenant_id);


--
-- Name: ix_dispositions_object_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dispositions_object_id ON public.dispositions USING btree (object_id);


--
-- Name: ix_dispositions_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dispositions_scope_line_id ON public.dispositions USING btree (scope_line_id);


--
-- Name: ix_dispositions_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dispositions_site_id ON public.dispositions USING btree (site_id);


--
-- Name: ix_dispositions_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dispositions_tenant_id ON public.dispositions USING btree (tenant_id);


--
-- Name: ix_distributions_record_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_distributions_record_id ON public.distributions USING btree (record_id);


--
-- Name: ix_distributions_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_distributions_scope_line_id ON public.distributions USING btree (scope_line_id);


--
-- Name: ix_distributions_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_distributions_site_id ON public.distributions USING btree (site_id);


--
-- Name: ix_distributions_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_distributions_status ON public.distributions USING btree (status);


--
-- Name: ix_distributions_target_node; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_distributions_target_node ON public.distributions USING btree (target_node);


--
-- Name: ix_distributions_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_distributions_tenant_id ON public.distributions USING btree (tenant_id);


--
-- Name: ix_evidence_replication_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_evidence_replication_status ON public.evidence USING btree (replication_status);


--
-- Name: ix_evidence_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_evidence_scope_line_id ON public.evidence USING btree (scope_line_id);


--
-- Name: ix_evidence_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_evidence_site_id ON public.evidence USING btree (site_id);


--
-- Name: ix_evidence_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_evidence_tenant_id ON public.evidence USING btree (tenant_id);


--
-- Name: ix_inbox_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inbox_scope_line_id ON public.inbox USING btree (scope_line_id);


--
-- Name: ix_inbox_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inbox_site_id ON public.inbox USING btree (site_id);


--
-- Name: ix_inbox_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inbox_tenant_id ON public.inbox USING btree (tenant_id);


--
-- Name: ix_inspection_object_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inspection_object_time ON public.inspections USING btree (tenant_id, site_id, object_id, occurred_at);


--
-- Name: ix_inspections_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inspections_line_id ON public.inspections USING btree (line_id);


--
-- Name: ix_inspections_occurred_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inspections_occurred_at ON public.inspections USING btree (occurred_at);


--
-- Name: ix_inspections_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inspections_scope_line_id ON public.inspections USING btree (scope_line_id);


--
-- Name: ix_inspections_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inspections_site_id ON public.inspections USING btree (site_id);


--
-- Name: ix_inspections_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inspections_tenant_id ON public.inspections USING btree (tenant_id);


--
-- Name: ix_knowledge_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_knowledge_scope_line_id ON public.knowledge USING btree (scope_line_id);


--
-- Name: ix_knowledge_search; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_knowledge_search ON public.knowledge USING gin (to_tsvector('simple'::regconfig, (((title)::text || ' '::text) || content)));


--
-- Name: ix_knowledge_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_knowledge_site_id ON public.knowledge USING btree (site_id);


--
-- Name: ix_knowledge_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_knowledge_tenant_id ON public.knowledge USING btree (tenant_id);


--
-- Name: ix_knowledge_vectors_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_knowledge_vectors_scope_line_id ON public.knowledge_vectors USING btree (scope_line_id);


--
-- Name: ix_knowledge_vectors_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_knowledge_vectors_site_id ON public.knowledge_vectors USING btree (site_id);


--
-- Name: ix_knowledge_vectors_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_knowledge_vectors_tenant_id ON public.knowledge_vectors USING btree (tenant_id);


--
-- Name: ix_lab_results_object_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lab_results_object_id ON public.lab_results USING btree (object_id);


--
-- Name: ix_lab_results_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lab_results_scope_line_id ON public.lab_results USING btree (scope_line_id);


--
-- Name: ix_lab_results_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lab_results_site_id ON public.lab_results USING btree (site_id);


--
-- Name: ix_lab_results_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lab_results_tenant_id ON public.lab_results USING btree (tenant_id);


--
-- Name: ix_learning_active_root; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_active_root ON public.learning_records USING btree (tenant_id, site_id, kind, status, ((snapshot ->> 'root_id'::text)));


--
-- Name: ix_learning_queue; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_queue ON public.learning_records USING btree (tenant_id, site_id, kind, status, created_at);


--
-- Name: ix_learning_records_case_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_records_case_id ON public.learning_records USING btree (case_id);


--
-- Name: ix_learning_records_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_records_kind ON public.learning_records USING btree (kind);


--
-- Name: ix_learning_records_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_records_scope_line_id ON public.learning_records USING btree (scope_line_id);


--
-- Name: ix_learning_records_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_records_site_id ON public.learning_records USING btree (site_id);


--
-- Name: ix_learning_records_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_records_status ON public.learning_records USING btree (status);


--
-- Name: ix_learning_records_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_records_tenant_id ON public.learning_records USING btree (tenant_id);


--
-- Name: ix_learning_tasks_record_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_tasks_record_id ON public.learning_tasks USING btree (record_id);


--
-- Name: ix_learning_tasks_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_tasks_scope_line_id ON public.learning_tasks USING btree (scope_line_id);


--
-- Name: ix_learning_tasks_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_tasks_site_id ON public.learning_tasks USING btree (site_id);


--
-- Name: ix_learning_tasks_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_tasks_status ON public.learning_tasks USING btree (status);


--
-- Name: ix_learning_tasks_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_tasks_tenant_id ON public.learning_tasks USING btree (tenant_id);


--
-- Name: ix_masters_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_masters_kind ON public.masters USING btree (kind);


--
-- Name: ix_masters_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_masters_scope_line_id ON public.masters USING btree (scope_line_id);


--
-- Name: ix_masters_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_masters_site_id ON public.masters USING btree (site_id);


--
-- Name: ix_masters_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_masters_tenant_id ON public.masters USING btree (tenant_id);


--
-- Name: ix_measurement_history; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_measurement_history ON public.measurements USING btree (tenant_id, site_id, equipment_id, metric, occurred_at);


--
-- Name: ix_measurements_equipment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_measurements_equipment_id ON public.measurements USING btree (equipment_id);


--
-- Name: ix_measurements_occurred_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_measurements_occurred_at ON public.measurements USING btree (occurred_at);


--
-- Name: ix_measurements_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_measurements_scope_line_id ON public.measurements USING btree (scope_line_id);


--
-- Name: ix_measurements_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_measurements_site_id ON public.measurements USING btree (site_id);


--
-- Name: ix_measurements_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_measurements_tenant_id ON public.measurements USING btree (tenant_id);


--
-- Name: ix_outbox_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outbox_scope_line_id ON public.outbox USING btree (scope_line_id);


--
-- Name: ix_outbox_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outbox_site_id ON public.outbox USING btree (site_id);


--
-- Name: ix_outbox_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outbox_status ON public.outbox USING btree (status);


--
-- Name: ix_outbox_synced; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outbox_synced ON public.outbox USING btree (synced);


--
-- Name: ix_outbox_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outbox_tenant_id ON public.outbox USING btree (tenant_id);


--
-- Name: ix_passages_entered_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_passages_entered_at ON public.passages USING btree (entered_at);


--
-- Name: ix_passages_object_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_passages_object_id ON public.passages USING btree (object_id);


--
-- Name: ix_passages_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_passages_scope_line_id ON public.passages USING btree (scope_line_id);


--
-- Name: ix_passages_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_passages_site_id ON public.passages USING btree (site_id);


--
-- Name: ix_passages_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_passages_tenant_id ON public.passages USING btree (tenant_id);


--
-- Name: ix_plans_case_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_plans_case_id ON public.plans USING btree (case_id);


--
-- Name: ix_plans_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_plans_scope_line_id ON public.plans USING btree (scope_line_id);


--
-- Name: ix_plans_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_plans_site_id ON public.plans USING btree (site_id);


--
-- Name: ix_plans_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_plans_tenant_id ON public.plans USING btree (tenant_id);


--
-- Name: ix_raw_records_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_raw_records_scope_line_id ON public.raw_records USING btree (scope_line_id);


--
-- Name: ix_raw_records_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_raw_records_site_id ON public.raw_records USING btree (site_id);


--
-- Name: ix_raw_records_source_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_raw_records_source_id ON public.raw_records USING btree (source_id);


--
-- Name: ix_raw_records_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_raw_records_status ON public.raw_records USING btree (status);


--
-- Name: ix_raw_records_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_raw_records_tenant_id ON public.raw_records USING btree (tenant_id);


--
-- Name: ix_receipts_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_receipts_scope_line_id ON public.receipts USING btree (scope_line_id);


--
-- Name: ix_receipts_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_receipts_site_id ON public.receipts USING btree (site_id);


--
-- Name: ix_receipts_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_receipts_tenant_id ON public.receipts USING btree (tenant_id);


--
-- Name: ix_remote_requests_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_remote_requests_scope_line_id ON public.remote_requests USING btree (scope_line_id);


--
-- Name: ix_remote_requests_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_remote_requests_site_id ON public.remote_requests USING btree (site_id);


--
-- Name: ix_remote_requests_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_remote_requests_tenant_id ON public.remote_requests USING btree (tenant_id);


--
-- Name: ix_retests_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_retests_scope_line_id ON public.retests USING btree (scope_line_id);


--
-- Name: ix_retests_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_retests_site_id ON public.retests USING btree (site_id);


--
-- Name: ix_retests_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_retests_tenant_id ON public.retests USING btree (tenant_id);


--
-- Name: ix_routes_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_routes_scope_line_id ON public.routes USING btree (scope_line_id);


--
-- Name: ix_routes_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_routes_site_id ON public.routes USING btree (site_id);


--
-- Name: ix_routes_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_routes_tenant_id ON public.routes USING btree (tenant_id);


--
-- Name: ix_runs_case_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_runs_case_id ON public.runs USING btree (case_id);


--
-- Name: ix_runs_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_runs_scope_line_id ON public.runs USING btree (scope_line_id);


--
-- Name: ix_runs_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_runs_site_id ON public.runs USING btree (site_id);


--
-- Name: ix_runs_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_runs_tenant_id ON public.runs USING btree (tenant_id);


--
-- Name: ix_sessions_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sessions_scope_line_id ON public.sessions USING btree (scope_line_id);


--
-- Name: ix_sessions_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sessions_site_id ON public.sessions USING btree (site_id);


--
-- Name: ix_sessions_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sessions_tenant_id ON public.sessions USING btree (tenant_id);


--
-- Name: ix_sites_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sites_scope_line_id ON public.sites USING btree (scope_line_id);


--
-- Name: ix_sites_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sites_site_id ON public.sites USING btree (site_id);


--
-- Name: ix_sites_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sites_tenant_id ON public.sites USING btree (tenant_id);


--
-- Name: ix_snapshots_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_snapshots_scope_line_id ON public.snapshots USING btree (scope_line_id);


--
-- Name: ix_snapshots_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_snapshots_site_id ON public.snapshots USING btree (site_id);


--
-- Name: ix_snapshots_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_snapshots_tenant_id ON public.snapshots USING btree (tenant_id);


--
-- Name: ix_study_groups_dataset_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_study_groups_dataset_id ON public.study_groups USING btree (dataset_id);


--
-- Name: ix_study_groups_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_study_groups_scope_line_id ON public.study_groups USING btree (scope_line_id);


--
-- Name: ix_study_groups_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_study_groups_site_id ON public.study_groups USING btree (site_id);


--
-- Name: ix_study_groups_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_study_groups_tenant_id ON public.study_groups USING btree (tenant_id);


--
-- Name: ix_users_scope_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_scope_line_id ON public.users USING btree (scope_line_id);


--
-- Name: ix_users_site_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_site_id ON public.users USING btree (site_id);


--
-- Name: ix_users_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_tenant_id ON public.users USING btree (tenant_id);


--
-- Name: audit quality_audit_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER quality_audit_immutable BEFORE DELETE OR UPDATE ON public.audit FOR EACH ROW EXECUTE FUNCTION public.quality_audit_append_only();


--
-- Name: approvals approvals_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT approvals_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id);


--
-- Name: approvals approvals_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT approvals_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES public.plans(id);


--
-- Name: dispositions dispositions_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dispositions
    ADD CONSTRAINT dispositions_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id);


--
-- Name: dispositions dispositions_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dispositions
    ADD CONSTRAINT dispositions_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.routes(id);


--
-- Name: knowledge knowledge_previous_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge
    ADD CONSTRAINT knowledge_previous_id_fkey FOREIGN KEY (previous_id) REFERENCES public.knowledge(id);


--
-- Name: knowledge_vectors knowledge_vectors_knowledge_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_vectors
    ADD CONSTRAINT knowledge_vectors_knowledge_id_fkey FOREIGN KEY (knowledge_id) REFERENCES public.knowledge(id);


--
-- Name: learning_records learning_records_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.learning_records
    ADD CONSTRAINT learning_records_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id);


--
-- Name: learning_records learning_records_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.learning_records
    ADD CONSTRAINT learning_records_file_id_fkey FOREIGN KEY (file_id) REFERENCES public.evidence(id);


--
-- Name: learning_records learning_records_knowledge_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.learning_records
    ADD CONSTRAINT learning_records_knowledge_id_fkey FOREIGN KEY (knowledge_id) REFERENCES public.knowledge(id);


--
-- Name: learning_records learning_records_previous_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.learning_records
    ADD CONSTRAINT learning_records_previous_id_fkey FOREIGN KEY (previous_id) REFERENCES public.learning_records(id);


--
-- Name: learning_tasks learning_tasks_record_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.learning_tasks
    ADD CONSTRAINT learning_tasks_record_id_fkey FOREIGN KEY (record_id) REFERENCES public.learning_records(id);


--
-- Name: masters masters_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.masters
    ADD CONSTRAINT masters_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.masters(id);


--
-- Name: plans plans_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plans
    ADD CONSTRAINT plans_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id);


--
-- Name: receipts receipts_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.receipts
    ADD CONSTRAINT receipts_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id);


--
-- Name: receipts receipts_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.receipts
    ADD CONSTRAINT receipts_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES public.plans(id);


--
-- Name: retests retests_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.retests
    ADD CONSTRAINT retests_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id);


--
-- Name: retests retests_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.retests
    ADD CONSTRAINT retests_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES public.plans(id);


--
-- Name: routes routes_previous_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routes
    ADD CONSTRAINT routes_previous_id_fkey FOREIGN KEY (previous_id) REFERENCES public.routes(id);


--
-- Name: runs runs_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT runs_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id);


--
-- Name: sessions sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: study_groups study_groups_dataset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.study_groups
    ADD CONSTRAINT study_groups_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES public.learning_records(id);


--
-- Name: adapters; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.adapters ENABLE ROW LEVEL SECURITY;

--
-- Name: approvals; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.approvals ENABLE ROW LEVEL SECURITY;

--
-- Name: audit; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.audit ENABLE ROW LEVEL SECURITY;

--
-- Name: cases; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cases ENABLE ROW LEVEL SECURITY;

--
-- Name: commands; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.commands ENABLE ROW LEVEL SECURITY;

--
-- Name: dispositions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.dispositions ENABLE ROW LEVEL SECURITY;

--
-- Name: distributions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.distributions ENABLE ROW LEVEL SECURITY;

--
-- Name: evidence; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.evidence ENABLE ROW LEVEL SECURITY;

--
-- Name: inbox; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.inbox ENABLE ROW LEVEL SECURITY;

--
-- Name: inspections; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.inspections ENABLE ROW LEVEL SECURITY;

--
-- Name: knowledge; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.knowledge ENABLE ROW LEVEL SECURITY;

--
-- Name: knowledge_vectors; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.knowledge_vectors ENABLE ROW LEVEL SECURITY;

--
-- Name: lab_results; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.lab_results ENABLE ROW LEVEL SECURITY;

--
-- Name: learning_records; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.learning_records ENABLE ROW LEVEL SECURITY;

--
-- Name: learning_tasks; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.learning_tasks ENABLE ROW LEVEL SECURITY;

--
-- Name: masters; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.masters ENABLE ROW LEVEL SECURITY;

--
-- Name: measurements; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.measurements ENABLE ROW LEVEL SECURITY;

--
-- Name: outbox; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.outbox ENABLE ROW LEVEL SECURITY;

--
-- Name: passages; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.passages ENABLE ROW LEVEL SECURITY;

--
-- Name: plans; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.plans ENABLE ROW LEVEL SECURITY;

--
-- Name: raw_records; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.raw_records ENABLE ROW LEVEL SECURITY;

--
-- Name: receipts; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.receipts ENABLE ROW LEVEL SECURITY;

--
-- Name: remote_requests; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.remote_requests ENABLE ROW LEVEL SECURITY;

--
-- Name: retests; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.retests ENABLE ROW LEVEL SECURITY;

--
-- Name: routes; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.routes ENABLE ROW LEVEL SECURITY;

--
-- Name: runs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.runs ENABLE ROW LEVEL SECURITY;

--
-- Name: adapters scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.adapters USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: approvals scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.approvals USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: audit scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.audit USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: cases scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.cases USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: commands scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.commands USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: dispositions scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.dispositions USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: distributions scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.distributions USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: evidence scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.evidence USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: inbox scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.inbox USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: inspections scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.inspections USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: knowledge scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.knowledge USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text)))) AND ((COALESCE(jsonb_array_length(((scope)::jsonb -> 'factory_ids'::text)), 0) = 0) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR (EXISTS ( SELECT 1
   FROM jsonb_array_elements_text(((knowledge.scope)::jsonb -> 'factory_ids'::text)) factories(value)
  WHERE (factories.value = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))))) AND ((COALESCE(jsonb_array_length(((scope)::jsonb -> 'line_ids'::text)), 0) = 0) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR (EXISTS ( SELECT 1
   FROM jsonb_array_elements_text(((knowledge.scope)::jsonb -> 'line_ids'::text)) allowed(value)
  WHERE (allowed.value = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))))));


--
-- Name: knowledge_vectors scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.knowledge_vectors USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text)))) AND (EXISTS ( SELECT 1
   FROM public.knowledge
  WHERE ((knowledge.id)::text = (knowledge_vectors.knowledge_id)::text)))));


--
-- Name: lab_results scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.lab_results USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: learning_records scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.learning_records USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: learning_tasks scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.learning_tasks USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: masters scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.masters USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: measurements scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.measurements USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: outbox scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.outbox USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: passages scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.passages USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: plans scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.plans USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: raw_records scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.raw_records USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: receipts scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.receipts USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: remote_requests scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.remote_requests USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: retests scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.retests USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: routes scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.routes USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: runs scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.runs USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: sessions scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.sessions USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: sites scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.sites USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: snapshots scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.snapshots USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: study_groups scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.study_groups USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: users scope_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY scope_isolation ON public.users USING ((((tenant_id)::text = current_setting('app.tenant'::text, true)) AND (((site_id)::text = ''::text) OR ((site_id)::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.sites'::text, true), ','::text)))) AND (((scope_line_id)::text = ''::text) OR ((scope_line_id)::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))) OR ('*'::text = ANY (string_to_array(current_setting('app.lines'::text, true), ','::text))))));


--
-- Name: sessions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.sessions ENABLE ROW LEVEL SECURITY;

--
-- Name: sites; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.sites ENABLE ROW LEVEL SECURITY;

--
-- Name: snapshots; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.snapshots ENABLE ROW LEVEL SECURITY;

--
-- Name: study_groups; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.study_groups ENABLE ROW LEVEL SECURITY;

--
-- Name: users; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

--
-- PostgreSQL database dump complete
--

