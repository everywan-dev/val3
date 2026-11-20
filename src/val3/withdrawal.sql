-- Entries taken out of delivery for good (design 2026-10-13-withdrawal-design.md). A withdrawal is final.
-- Run with search_path set to the vault and public.
CREATE TABLE withdrawal (
    entry_id bigint PRIMARY KEY REFERENCES entry (id),
    cause text NOT NULL CONSTRAINT withdrawal_cause_check CHECK (cause IN ('wrong', 'obsolete', 'duplicate')),
    reason text NOT NULL CHECK (length(btrim(reason)) > 0),
    withdrawn_by text NOT NULL,
    review_id bigint REFERENCES review_item (id),
    batch text,
    evidence jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON withdrawal
    FOR EACH ROW EXECUTE FUNCTION val3_common.forbid_change();
CREATE TRIGGER append_only_truncate BEFORE TRUNCATE ON withdrawal
    FOR EACH STATEMENT EXECUTE FUNCTION val3_common.forbid_change();
