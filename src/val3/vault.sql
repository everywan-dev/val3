CREATE TABLE entry (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('fact', 'recipe', 'preference', 'decision', 'tool')),
    title text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
    statement text NOT NULL CHECK (length(statement) > 0),
    keywords text NOT NULL DEFAULT '',
    content_hash text NOT NULL UNIQUE,
    origin_project text NOT NULL,
    origin_session text,
    origin_machine text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    search_en tsvector GENERATED ALWAYS AS
        (to_tsvector('val3_common.en'::regconfig, title || ' ' || statement || ' ' || keywords)) STORED,
    search_es tsvector GENERATED ALWAYS AS
        (to_tsvector('val3_common.es'::regconfig, title || ' ' || statement || ' ' || keywords)) STORED
);
CREATE INDEX entry_search_en ON entry USING gin (search_en);
CREATE INDEX entry_search_es ON entry USING gin (search_es);

CREATE TABLE supersession (
    old_id bigint PRIMARY KEY REFERENCES entry (id),
    new_id bigint NOT NULL REFERENCES entry (id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (old_id <> new_id)
);

CREATE TABLE knowledge_index (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL UNIQUE CHECK (name ~ '^[a-z0-9][a-z0-9-]{0,62}$')
);

CREATE TABLE entry_index (
    entry_id bigint NOT NULL REFERENCES entry (id),
    index_id bigint NOT NULL REFERENCES knowledge_index (id),
    PRIMARY KEY (entry_id, index_id)
);

CREATE TABLE evidence (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entry_id bigint NOT NULL REFERENCES entry (id),
    kind text NOT NULL CONSTRAINT evidence_kind_check
        CHECK (kind IN ('query', 'command', 'api', 'test', 'user', 'file')),
    reproduction text NOT NULL DEFAULT '',
    quote text NOT NULL,
    session_id text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX evidence_entry ON evidence (entry_id);

CREATE TABLE tool (
    entry_id bigint PRIMARY KEY REFERENCES entry (id),
    tool_type text NOT NULL CHECK (tool_type IN ('skill', 'mcp', 'cli')),
    environment text NOT NULL DEFAULT '',
    priority integer NOT NULL DEFAULT 0
);

CREATE TABLE recipe_parameter (
    recipe_id bigint NOT NULL REFERENCES entry (id),
    name text NOT NULL,
    description text NOT NULL DEFAULT '',
    PRIMARY KEY (recipe_id, name)
);

CREATE TABLE recipe_step (
    recipe_id bigint NOT NULL REFERENCES entry (id),
    position integer NOT NULL CHECK (position > 0),
    action text NOT NULL,
    tool_id bigint REFERENCES entry (id),
    PRIMARY KEY (recipe_id, position)
);

CREATE TABLE step_fact (
    recipe_id bigint NOT NULL,
    position integer NOT NULL,
    fact_id bigint NOT NULL REFERENCES entry (id),
    PRIMARY KEY (recipe_id, position, fact_id),
    FOREIGN KEY (recipe_id, position) REFERENCES recipe_step (recipe_id, position)
);
CREATE INDEX step_fact_fact ON step_fact (fact_id);

CREATE TABLE recipe_example (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recipe_id bigint NOT NULL REFERENCES entry (id),
    parameters jsonb NOT NULL,
    result text NOT NULL,
    session_id text
);

CREATE TABLE review_item (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reason text NOT NULL CONSTRAINT review_item_reason_check
        CHECK (reason IN ('recipe', 'tool', 'user-confirmation', 'supersession', 'recipe-cites-superseded',
                          'import', 'withdrawal', 'withdrawn', 'no-evidence')),
    payload jsonb NOT NULL,
    content_hash text NOT NULL UNIQUE,
    entry_id bigint REFERENCES entry (id),
    origin_project text NOT NULL,
    origin_session text,
    origin_machine text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE review_decision (
    review_id bigint PRIMARY KEY REFERENCES review_item (id),
    decision text NOT NULL CONSTRAINT review_decision_decision_check
        CHECK (decision IN ('approved', 'rejected', 'retired')),
    decided_by text NOT NULL,
    entry_id bigint REFERENCES entry (id),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE event (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at timestamptz NOT NULL DEFAULT now(),
    action text NOT NULL,
    entry_id bigint,
    review_id bigint,
    project text NOT NULL,
    session_id text,
    machine text NOT NULL,
    detail jsonb NOT NULL DEFAULT '{}'
);

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['entry', 'supersession', 'knowledge_index', 'entry_index', 'evidence',
                             'tool', 'recipe_parameter', 'recipe_step', 'step_fact', 'recipe_example',
                             'review_item', 'review_decision', 'event'] LOOP
        EXECUTE format('CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION val3_common.forbid_change()', t);
        EXECUTE format('CREATE TRIGGER append_only_truncate BEFORE TRUNCATE ON %I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION val3_common.forbid_change()', t);
    END LOOP;
END $$;
