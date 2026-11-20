-- Entries a person vouched for (ADR 0012 §1): attested is not verified. A vault holding this table runs the rules
-- of ADR 0012 (no review queue); `vault upgrade` creates it and attests every entry without evidence.
-- Run with search_path set to the vault and public.
CREATE TABLE attestation (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entry_id bigint NOT NULL REFERENCES entry (id),
    attested_by text NOT NULL,
    how text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX attestation_entry ON attestation (entry_id);
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON attestation
    FOR EACH ROW EXECUTE FUNCTION val3_common.forbid_change();
CREATE TRIGGER append_only_truncate BEFORE TRUNCATE ON attestation
    FOR EACH STATEMENT EXECUTE FUNCTION val3_common.forbid_change();
