-- Index names merged into another index (design 2026-10-21-visibility-design.md D6): an alias resolves to its
-- target on every read and write. Run with search_path set to the vault and public.
CREATE TABLE index_alias (
    name text PRIMARY KEY CHECK (name ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
    target_id bigint NOT NULL REFERENCES knowledge_index (id),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON index_alias
    FOR EACH ROW EXECUTE FUNCTION val3_common.forbid_change();
CREATE TRIGGER append_only_truncate BEFORE TRUNCATE ON index_alias
    FOR EACH STATEMENT EXECUTE FUNCTION val3_common.forbid_change();
