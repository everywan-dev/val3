-- Close neighbours (design 2026-10-24-automatic-admission-design.md D2-D4): pairs of live entries of one kind
-- whose vectors reach `[embedding] neighbour`, and how each pair was settled. Entries in neighbour_exempt
-- (there when the vault was upgraded, or admitted without `[embedding]`) are never paired with each other.
-- Run with search_path set to the vault and public.
CREATE TABLE neighbour_exempt (
    entry_id bigint PRIMARY KEY REFERENCES entry (id),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE neighbour (
    entry_id bigint NOT NULL REFERENCES entry (id),
    other_id bigint NOT NULL REFERENCES entry (id),
    score real NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (entry_id, other_id),
    CHECK (entry_id > other_id)
);
CREATE TABLE settlement (
    entry_id bigint NOT NULL,
    other_id bigint NOT NULL,
    outcome text NOT NULL CHECK (outcome IN ('replace', 'drop', 'keep')),
    settled_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (entry_id, other_id),
    FOREIGN KEY (entry_id, other_id) REFERENCES neighbour (entry_id, other_id)
);
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON neighbour_exempt
    FOR EACH ROW EXECUTE FUNCTION val3_common.forbid_change();
CREATE TRIGGER append_only_truncate BEFORE TRUNCATE ON neighbour_exempt
    FOR EACH STATEMENT EXECUTE FUNCTION val3_common.forbid_change();
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON neighbour
    FOR EACH ROW EXECUTE FUNCTION val3_common.forbid_change();
CREATE TRIGGER append_only_truncate BEFORE TRUNCATE ON neighbour
    FOR EACH STATEMENT EXECUTE FUNCTION val3_common.forbid_change();
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON settlement
    FOR EACH ROW EXECUTE FUNCTION val3_common.forbid_change();
CREATE TRIGGER append_only_truncate BEFORE TRUNCATE ON settlement
    FOR EACH STATEMENT EXECUTE FUNCTION val3_common.forbid_change();
