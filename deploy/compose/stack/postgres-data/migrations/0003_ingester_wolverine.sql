-- Wolverine's durable message tables in ingest_ops (docs/architecture/data-plane.md, section 3),
-- generated from the ingester's own settings (src/dotnet/AetherSpark.Ingester/Messaging.cs), never edited:
--   dotnet run --project src/dotnet/AetherSpark.Ingester -- db-dump <file>
-- Wolverine creates nothing itself (AutoCreate.None); the ingester refuses to start when these
-- tables differ from what its Wolverine version expects, and a new version's changes come as a new file.
DO LANGUAGE plpgsql $tran$
BEGIN

DO $$
BEGIN      IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = 'ingest_ops') THEN
        BEGIN
          EXECUTE 'CREATE SCHEMA IF NOT EXISTS ingest_ops';
        EXCEPTION
          WHEN duplicate_schema THEN NULL;
          WHEN unique_violation THEN NULL;
        END;
      END IF;

END
$$;


CREATE TABLE IF NOT EXISTS ingest_ops.wolverine_outgoing_envelopes (
    id              uuid                        NOT NULL,
    owner_id        integer                     NOT NULL,
    destination     varchar                     NOT NULL,
    deliver_by      timestamp with time zone    NULL,
    body            bytea                       NOT NULL,
    attempts        integer                     NULL DEFAULT 0,
    message_type    varchar                     NOT NULL,
CONSTRAINT pkey_wolverine_outgoing_envelopes_id PRIMARY KEY (id)
);

CREATE INDEX idx_wolverine_outgoing_envelopes_owner ON ingest_ops.wolverine_outgoing_envelopes USING btree (owner_id) WHERE (owner_id <> 0);

CREATE INDEX idx_wolverine_outgoing_envelopes_recover ON ingest_ops.wolverine_outgoing_envelopes USING btree (destination) WHERE (owner_id = 0);
CREATE TABLE IF NOT EXISTS ingest_ops.wolverine_incoming_envelopes (
    id                uuid                        NOT NULL,
    status            varchar                     NOT NULL,
    owner_id          integer                     NOT NULL,
    execution_time    timestamp with time zone    NULL DEFAULT NULL,
    attempts          integer                     NULL DEFAULT 0,
    body              bytea                       NOT NULL,
    message_type      varchar                     NOT NULL,
    received_at       varchar                     NULL,
    keep_until        timestamp with time zone    NULL,
CONSTRAINT pkey_wolverine_incoming_envelopes_id PRIMARY KEY (id)
);

CREATE INDEX idx_wolverine_incoming_envelopes_owner ON ingest_ops.wolverine_incoming_envelopes USING btree (owner_id) WHERE (owner_id <> 0);

CREATE INDEX idx_wolverine_incoming_envelopes_recover ON ingest_ops.wolverine_incoming_envelopes USING btree (received_at) WHERE (status = 'Incoming' AND owner_id = 0);

CREATE INDEX idx_wolverine_incoming_envelopes_keep_until ON ingest_ops.wolverine_incoming_envelopes USING btree (keep_until) WHERE (status = 'Handled');

CREATE INDEX idx_wolverine_incoming_envelopes_scheduled ON ingest_ops.wolverine_incoming_envelopes USING btree (execution_time) WHERE (status = 'Scheduled');
CREATE TABLE IF NOT EXISTS ingest_ops.wolverine_dead_letters (
    id                   uuid                        NOT NULL,
    execution_time       timestamp with time zone    NULL DEFAULT NULL,
    body                 bytea                       NOT NULL,
    message_type         varchar                     NOT NULL,
    received_at          varchar                     NULL,
    source               varchar                     NULL,
    exception_type       varchar                     NULL,
    exception_message    varchar                     NULL,
    sent_at              timestamp with time zone    NULL,
    replayable           boolean                     NULL,
    failure_group        varchar(100)                NULL,
    failure_title        varchar(500)                NULL,
    failed_at            timestamp with time zone    NULL,
    first_failed_at      timestamp with time zone    NULL,
    replay_count         integer                     NULL,
    last_replayed_at     timestamp with time zone    NULL,
    archived_at          timestamp with time zone    NULL,
CONSTRAINT pkey_wolverine_dead_letters_id PRIMARY KEY (id)
);

CREATE INDEX idx_wolverine_dead_letters_replayable ON ingest_ops.wolverine_dead_letters USING btree (replayable) WHERE (replayable = true);

CREATE INDEX idx_wolverine_dead_letters_failure_group ON ingest_ops.wolverine_dead_letters USING btree (failure_group);
CREATE TABLE IF NOT EXISTS ingest_ops.wolverine_dead_letter_history (
    id                  uuid                        NOT NULL,
    replay_count        integer                     NULL,
    last_replayed_at    timestamp with time zone    NULL,
    first_failed_at     timestamp with time zone    NULL,
CONSTRAINT pkey_wolverine_dead_letter_history_id PRIMARY KEY (id)
);

CREATE INDEX idx_wolverine_dead_letter_history_last_replayed_at ON ingest_ops.wolverine_dead_letter_history USING btree (last_replayed_at);
CREATE TABLE IF NOT EXISTS ingest_ops.wolverine_nodes (
    id              uuid                        NOT NULL,
    node_number     serial                      NOT NULL,
    description     varchar                     NOT NULL,
    uri             varchar                     NOT NULL,
    started         timestamp with time zone    NOT NULL DEFAULT now(),
    health_check    timestamp with time zone    NOT NULL DEFAULT now(),
    version         varchar                     NULL,
    capabilities    text[]                      NULL,
CONSTRAINT pkey_wolverine_nodes_id PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS ingest_ops.wolverine_node_assignments (
    id         varchar                     NOT NULL,
    node_id    uuid                        NULL,
    started    timestamp with time zone    NOT NULL DEFAULT now(),
CONSTRAINT pkey_wolverine_node_assignments_id PRIMARY KEY (id)
);

DO $do$
BEGIN
ALTER TABLE ingest_ops.wolverine_node_assignments
ADD CONSTRAINT fkey_wolverine_node_assignments_node_id FOREIGN KEY(node_id)
REFERENCES ingest_ops.wolverine_nodes(id)ON DELETE CASCADE
;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$do$;
CREATE TABLE IF NOT EXISTS ingest_ops.wolverine_control_queue (
    id              uuid                        NOT NULL,
    message_type    varchar                     NOT NULL,
    node_id         uuid                        NOT NULL,
    body            bytea                       NOT NULL,
    posted          timestamp with time zone    NOT NULL DEFAULT NOW(),
    expires         timestamp with time zone    NULL,
CONSTRAINT pkey_wolverine_control_queue_id PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS ingest_ops.wolverine_node_records (
    id             serial                      NOT NULL,
    node_number    integer                     NOT NULL,
    event_name     varchar                     NOT NULL,
    timestamp      timestamp with time zone    NOT NULL DEFAULT now(),
    description    varchar                     NULL,
CONSTRAINT pkey_wolverine_node_records_id PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS ingest_ops.wolverine_agent_restrictions (
    id      uuid       NOT NULL,
    uri     varchar    NOT NULL,
    type    varchar    NOT NULL,
    node    integer    NOT NULL DEFAULT 0,
CONSTRAINT pkey_wolverine_agent_restrictions_id PRIMARY KEY (id)
);

END;
$tran$;
