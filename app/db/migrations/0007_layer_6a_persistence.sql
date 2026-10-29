-- 0007: Durable Layer 6A records for model metadata, documents and AI assessment.
-- Binary files remain in controlled object storage; identity mappings remain
-- exclusively in the restricted identity schema.

CREATE TABLE core.model_versions (
    model_version_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    institution_id text NOT NULL REFERENCES core.institutions(institution_id) ON DELETE RESTRICT,
    model_name text NOT NULL CHECK (length(btrim(model_name)) BETWEEN 1 AND 128),
    version text NOT NULL CHECK (length(btrim(version)) BETWEEN 1 AND 128),
    registry_ref text,
    artifact_sha256 text CHECK (artifact_sha256 IS NULL OR artifact_sha256 ~ '^[0-9a-f]{64}$'),
    status text NOT NULL DEFAULT 'registered' CHECK (status IN ('registered','active','retired','failed')),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    registered_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (institution_id, model_version_id),
    UNIQUE (institution_id, model_name, version)
);
CREATE INDEX model_versions_status_idx ON core.model_versions (institution_id, status, registered_at);

CREATE TABLE core.documents (
    document_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    institution_id text NOT NULL REFERENCES core.institutions(institution_id) ON DELETE RESTRICT,
    assignment_id uuid,
    uploader_pseudonymous_id text,
    display_name text NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 512),
    mime_type text NOT NULL CHECK (length(mime_type) BETWEEN 1 AND 255),
    size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
    storage_key text NOT NULL CHECK (length(btrim(storage_key)) BETWEEN 1 AND 2048),
    content_sha256 text CHECK (content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','ready','failed','expired')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz,
    UNIQUE (institution_id, document_id),
    FOREIGN KEY (institution_id, assignment_id) REFERENCES core.assignments(institution_id, assignment_id) ON DELETE RESTRICT
);
CREATE INDEX documents_assignment_idx ON core.documents (institution_id, assignment_id, created_at);
CREATE INDEX documents_expiry_idx ON core.documents (institution_id, expires_at) WHERE expires_at IS NOT NULL;

CREATE TABLE core.document_extractions (
    extraction_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    institution_id text NOT NULL REFERENCES core.institutions(institution_id) ON DELETE RESTRICT,
    document_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','completed','failed','expired')),
    extractor_version text,
    extracted_text text CHECK (extracted_text IS NULL OR length(extracted_text) <= 2000000),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz,
    UNIQUE (institution_id, extraction_id),
    FOREIGN KEY (institution_id, document_id) REFERENCES core.documents(institution_id, document_id) ON DELETE RESTRICT
);
CREATE INDEX document_extractions_document_idx ON core.document_extractions (institution_id, document_id, created_at);
CREATE INDEX document_extractions_expiry_idx ON core.document_extractions (institution_id, expires_at) WHERE expires_at IS NOT NULL;

CREATE TABLE assessment.conversations (
    conversation_id text NOT NULL CHECK (length(conversation_id) BETWEEN 1 AND 256),
    institution_id text NOT NULL REFERENCES core.institutions(institution_id) ON DELETE RESTRICT,
    assignment_id uuid,
    actor_pseudonymous_id text,
    policy_version text NOT NULL CHECK (length(policy_version) BETWEEN 1 AND 128),
    policy_tier text NOT NULL CHECK (policy_tier IN ('YELLOW','GREEN')),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','completed','archived')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    PRIMARY KEY (institution_id, conversation_id),
    FOREIGN KEY (institution_id, assignment_id) REFERENCES core.assignments(institution_id, assignment_id) ON DELETE RESTRICT
);
CREATE INDEX conversations_actor_idx ON assessment.conversations (institution_id, actor_pseudonymous_id, created_at);
CREATE INDEX conversations_assignment_idx ON assessment.conversations (institution_id, assignment_id, created_at);

CREATE TABLE assessment.messages (
    message_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    institution_id text NOT NULL,
    conversation_id text NOT NULL,
    idempotency_key text,
    role text NOT NULL CHECK (role IN ('user','assistant')),
    content text NOT NULL CHECK (length(content) BETWEEN 1 AND 32000),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (institution_id, message_id),
    UNIQUE (institution_id, conversation_id, message_id),
    FOREIGN KEY (institution_id, conversation_id) REFERENCES assessment.conversations(institution_id, conversation_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX messages_idempotency_idx ON assessment.messages (institution_id, conversation_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX messages_order_idx ON assessment.messages (institution_id, conversation_id, created_at, message_id);

CREATE TABLE assessment.agent_runs (
    agent_run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    institution_id text NOT NULL,
    conversation_id text NOT NULL,
    model_version_id uuid,
    request_id text NOT NULL CHECK (length(request_id) BETWEEN 1 AND 128),
    policy_version text NOT NULL CHECK (length(policy_version) BETWEEN 1 AND 128),
    policy_tier text NOT NULL CHECK (policy_tier IN ('YELLOW','GREEN')),
    provider text NOT NULL CHECK (length(provider) BETWEEN 1 AND 64),
    model_name text NOT NULL CHECK (length(model_name) BETWEEN 1 AND 128),
    status text NOT NULL DEFAULT 'running' CHECK (status IN ('running','completed','failed')),
    error_category text CHECK (error_category IS NULL OR length(error_category) <= 128),
    input_tokens integer CHECK (input_tokens IS NULL OR input_tokens >= 0),
    output_tokens integer CHECK (output_tokens IS NULL OR output_tokens >= 0),
    result_sha256 text CHECK (result_sha256 IS NULL OR result_sha256 ~ '^[0-9a-f]{64}$'),
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    UNIQUE (institution_id, agent_run_id),
    UNIQUE (institution_id, conversation_id, agent_run_id),
    UNIQUE (institution_id, request_id),
    FOREIGN KEY (institution_id, conversation_id) REFERENCES assessment.conversations(institution_id, conversation_id) ON DELETE RESTRICT,
    FOREIGN KEY (institution_id, model_version_id) REFERENCES core.model_versions(institution_id, model_version_id) ON DELETE RESTRICT
);
CREATE INDEX agent_runs_conversation_idx ON assessment.agent_runs (institution_id, conversation_id, started_at);

CREATE TABLE assessment.feedback (
    feedback_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    institution_id text NOT NULL,
    conversation_id text NOT NULL,
    message_id uuid,
    agent_run_id uuid,
    actor_pseudonymous_id text,
    feedback_type text NOT NULL CHECK (feedback_type IN ('helpful','not_helpful','report','rating')),
    rating smallint CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
    comment text CHECK (comment IS NULL OR length(comment) <= 4000),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (institution_id, feedback_id),
    CHECK (message_id IS NOT NULL OR agent_run_id IS NOT NULL),
    CHECK ((feedback_type = 'rating' AND rating IS NOT NULL) OR (feedback_type <> 'rating' AND rating IS NULL)),
    FOREIGN KEY (institution_id, conversation_id) REFERENCES assessment.conversations(institution_id, conversation_id) ON DELETE RESTRICT,
    FOREIGN KEY (institution_id, conversation_id, message_id) REFERENCES assessment.messages(institution_id, conversation_id, message_id) ON DELETE RESTRICT,
    FOREIGN KEY (institution_id, conversation_id, agent_run_id) REFERENCES assessment.agent_runs(institution_id, conversation_id, agent_run_id) ON DELETE RESTRICT
);
CREATE INDEX feedback_conversation_idx ON assessment.feedback (institution_id, conversation_id, created_at);

DO $$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'core.model_versions','core.documents','core.document_extractions',
        'assessment.conversations','assessment.messages','assessment.agent_runs','assessment.feedback'
    ] LOOP
        EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', tbl);
        EXECUTE format('CREATE POLICY tenant_isolation ON %s USING (institution_id = current_setting(''app.institution_id'', true)) WITH CHECK (institution_id = current_setting(''app.institution_id'', true))', tbl);
    END LOOP;
END
$$;

GRANT SELECT, INSERT, UPDATE, DELETE ON core.model_versions, core.documents, core.document_extractions TO resonance_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON assessment.conversations, assessment.messages, assessment.agent_runs, assessment.feedback TO resonance_app;
GRANT SELECT ON core.model_versions, core.documents, core.document_extractions, assessment.conversations, assessment.messages, assessment.agent_runs, assessment.feedback TO resonance_backup;
