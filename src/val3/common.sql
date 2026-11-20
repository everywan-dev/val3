CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS val3_common;
GRANT USAGE ON SCHEMA val3_common TO PUBLIC;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config
                   WHERE cfgname = 'en' AND cfgnamespace = 'val3_common'::regnamespace) THEN
        CREATE TEXT SEARCH CONFIGURATION val3_common.en (COPY = pg_catalog.english);
        ALTER TEXT SEARCH CONFIGURATION val3_common.en
            ALTER MAPPING FOR hword, hword_part, word WITH unaccent, english_stem;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config
                   WHERE cfgname = 'es' AND cfgnamespace = 'val3_common'::regnamespace) THEN
        CREATE TEXT SEARCH CONFIGURATION val3_common.es (COPY = pg_catalog.spanish);
        ALTER TEXT SEARCH CONFIGURATION val3_common.es
            ALTER MAPPING FOR hword, hword_part, word WITH unaccent, spanish_stem;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION val3_common.forbid_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'val3 vaults are append-only: % on %.% is not allowed',
        TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME;
END $$;

DROP FUNCTION IF EXISTS val3_common.or_query(regconfig, regconfig, text);

-- Lexemes of `t` under `cfg`, leaving out words that are stop words in English or Spanish.
-- The check runs on the original word: the stock stop lists hold accented forms (más, también).
-- ts_debug only splits the words: its lexemes column stops at the first (filtering) dictionary.
CREATE OR REPLACE FUNCTION val3_common.query_lexemes(cfg regconfig, t text)
RETURNS text[] LANGUAGE sql STABLE AS $$
    SELECT coalesce(array_agg(DISTINCT lexeme), ARRAY[]::text[])
    FROM ts_debug(cfg, t) AS d, unnest(tsvector_to_array(to_tsvector(cfg, d.token))) AS lexeme
    WHERE to_tsvector('pg_catalog.english', d.token) <> ''::tsvector
      AND to_tsvector('pg_catalog.spanish', d.token) <> ''::tsvector
$$;

-- OR of the given lexemes, quoted for the tsquery parser.
CREATE OR REPLACE FUNCTION val3_common.or_query(lexemes text[])
RETURNS tsquery LANGUAGE sql IMMUTABLE AS $$
    SELECT string_agg('''' || replace(replace(l, '\', '\\'), '''', '''''') || '''', ' | ')::tsquery
    FROM unnest(lexemes) AS l
$$;

-- The vault catalogue (design 2026-11-07-vault-sharing-design.md §4.2): the latest note of a vault is its
-- description and parent. Only a role that can write a vault describes it; owners and superusers bypass RLS.
CREATE TABLE IF NOT EXISTS val3_common.vault_note (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    vault text NOT NULL,
    parent text,
    description text NOT NULL CHECK (length(description) BETWEEN 1 AND 300 AND description !~ '[\n\r]'),
    created_by text NOT NULL DEFAULT current_user,
    created_at timestamptz NOT NULL DEFAULT now()
);
-- names as check_vault_name takes them; dropped and added again so an older database gets the current form
ALTER TABLE val3_common.vault_note
    DROP CONSTRAINT IF EXISTS vault_note_vault_check,
    ADD CONSTRAINT vault_note_vault_check CHECK (vault ~ '^[a-z][a-z0-9_]{0,62}$' AND vault !~ '^pg_'),
    DROP CONSTRAINT IF EXISTS vault_note_parent_check,
    ADD CONSTRAINT vault_note_parent_check CHECK (parent ~ '^[a-z][a-z0-9_]{0,62}$' AND parent !~ '^pg_');
ALTER TABLE val3_common.vault_note ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS vault_note_read ON val3_common.vault_note;
CREATE POLICY vault_note_read ON val3_common.vault_note FOR SELECT USING (true);
DROP POLICY IF EXISTS vault_note_write ON val3_common.vault_note;
CREATE POLICY vault_note_write ON val3_common.vault_note FOR INSERT
    WITH CHECK (created_by = current_user AND has_table_privilege(format('%I.entry', vault), 'INSERT'));
GRANT SELECT, INSERT ON val3_common.vault_note TO PUBLIC;
GRANT USAGE ON SEQUENCE val3_common.vault_note_id_seq TO PUBLIC;

-- One row per mirror refresh (§4.4); written only by its owner, the operator that loads mirrors.
CREATE TABLE IF NOT EXISTS val3_common.mirror_sync (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    vault text NOT NULL,
    vault_id uuid NOT NULL,
    source text NOT NULL,
    rows jsonb NOT NULL,
    synced_by text NOT NULL DEFAULT current_user,
    synced_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT ON val3_common.mirror_sync TO PUBLIC;

CREATE OR REPLACE TRIGGER append_only BEFORE UPDATE OR DELETE ON val3_common.vault_note
    FOR EACH ROW EXECUTE FUNCTION val3_common.forbid_change();
CREATE OR REPLACE TRIGGER append_only_truncate BEFORE TRUNCATE ON val3_common.vault_note
    FOR EACH STATEMENT EXECUTE FUNCTION val3_common.forbid_change();
CREATE OR REPLACE TRIGGER append_only BEFORE UPDATE OR DELETE ON val3_common.mirror_sync
    FOR EACH ROW EXECUTE FUNCTION val3_common.forbid_change();
CREATE OR REPLACE TRIGGER append_only_truncate BEFORE TRUNCATE ON val3_common.mirror_sync
    FOR EACH STATEMENT EXECUTE FUNCTION val3_common.forbid_change();

-- On every table of a mirror: inserts only during a load (`val3.loading` on) by a member of the schema's owner.
CREATE OR REPLACE FUNCTION val3_common.mirror_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('val3.loading', true) IS DISTINCT FROM 'on'
       OR NOT pg_has_role(current_user,
                          (SELECT nspowner FROM pg_namespace WHERE nspname = TG_TABLE_SCHEMA), 'MEMBER') THEN
        RAISE EXCEPTION 'vault % is a mirror and read only', TG_TABLE_SCHEMA;
    END IF;
    RETURN NEW;
END $$;
