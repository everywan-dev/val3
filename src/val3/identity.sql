-- The vault's fixed identity (design 2026-11-07-vault-sharing-design.md §4.4): one row, inserted by
-- `vault create` and `vault upgrade`, never here, so a staging schema starts empty. A mirror keeps its source's.
-- Run with search_path set to the vault and public.
CREATE TABLE identity (
    vault_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX identity_one_row ON identity ((true));
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON identity
    FOR EACH ROW EXECUTE FUNCTION val3_common.forbid_change();
CREATE TRIGGER append_only_truncate BEFORE TRUNCATE ON identity
    FOR EACH STATEMENT EXECUTE FUNCTION val3_common.forbid_change();
