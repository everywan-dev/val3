-- Vectors of entries, one per embedding space: model, model digest and version of the embedded text.
-- Vectors of different spaces are never compared. Run with search_path set to the vault and public.
CREATE TABLE embedding (
    entry_id bigint NOT NULL REFERENCES entry (id),
    model text NOT NULL CHECK (length(model) BETWEEN 1 AND 200),
    digest text NOT NULL CHECK (length(digest) BETWEEN 1 AND 200),
    text_version smallint NOT NULL CHECK (text_version > 0),
    dims integer NOT NULL CHECK (dims > 0),
    vector vector NOT NULL CHECK (vector_dims(vector) = dims),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (entry_id, model, digest, text_version)
);
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON embedding
    FOR EACH ROW EXECUTE FUNCTION val3_common.forbid_change();
CREATE TRIGGER append_only_truncate BEFORE TRUNCATE ON embedding
    FOR EACH STATEMENT EXECUTE FUNCTION val3_common.forbid_change();
