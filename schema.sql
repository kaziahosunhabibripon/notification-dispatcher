-- Durable, PostgreSQL-backed work queue. This file is intentionally hand written.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE campaigns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    idempotency_key TEXT NOT NULL,
    channel VARCHAR(32) NOT NULL CHECK (channel IN ('sms', 'whatsapp', 'email', 'push')),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    send_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled', 'running', 'completed', 'cancelled')),
    recipient_count INTEGER NOT NULL DEFAULT 0 CHECK (recipient_count >= 0),
    cancelled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE campaign_recipients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    user_id UUID NOT NULL,
    destination TEXT NOT NULL CHECK (length(trim(destination)) > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (campaign_id, user_id)
);

CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    user_id UUID NOT NULL,
    destination TEXT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'accepted', 'failed', 'cancelled')),
    attempts SMALLINT NOT NULL DEFAULT 0 CHECK (attempts >= 0 AND attempts <= 5),
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_until TIMESTAMPTZ,
    lease_token UUID,
    last_error JSONB,
    provider_message_id TEXT,
    accepted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (campaign_id, user_id)
);

CREATE TABLE message_attempts (
    id BIGSERIAL PRIMARY KEY,
    message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    attempt_number SMALLINT NOT NULL,
    outcome VARCHAR(24) NOT NULL,
    http_status SMALLINT,
    error_code TEXT,
    detail TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Mock-provider persistence is separate from dispatcher state, but deliberately
-- shares this development database so provider idempotency survives restarts.
CREATE TABLE provider_acceptances (
    idempotency_key TEXT PRIMARY KEY,
    message_id UUID NOT NULL DEFAULT gen_random_uuid(),
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    request JSONB NOT NULL
);

CREATE INDEX campaigns_due_idx ON campaigns (send_at) WHERE status IN ('scheduled', 'running');
CREATE INDEX recipients_campaign_idx ON campaign_recipients (campaign_id, id);
CREATE INDEX messages_claim_idx ON messages (next_attempt_at, created_at) WHERE status IN ('pending', 'processing');
CREATE INDEX messages_campaign_status_idx ON messages (campaign_id, status);
CREATE INDEX messages_lease_idx ON messages (lease_until) WHERE status = 'processing';
CREATE INDEX message_attempts_message_idx ON message_attempts (message_id, created_at DESC);
