# ADR 0003 — Imported knowledge is checked against repository files and approved by a person

Date: 2026-10-12. Status: accepted. Amends ADR 0001.

## Context

ADR 0001 admits a fact when its evidence is quoted literally from the transcript, and then trusts it.
validated-memory 2.x adopters hold knowledge with no transcript: units cite `<path> at <sha>`, notes
name files. A literal quote from a file at a commit proves what the repository recorded then, and can
be re-checked as long as the object is kept. It does not prove the statement: on the first adopter a
unit states a measurement none of its cited files holds, two notes describe a component retired by a
later ADR whose cited text is unchanged, and every cited file and note was last changed in a commit
with an agent co-author.

## Decision

- A new evidence kind, `file`: a quote checked by `val3 import` with `git show <commit>:<path>`, the
  commit fixed by the importer (provenance, or the note's last change), never by the model.
- Only `val3 import` produces it, and every imported candidate goes to the review list. Capture and
  `val3 save` do not accept it.
- An approved import item may carry no evidence; the review decision is its attestation.
- Quotes are checked once, at import; recall does not re-read the repository.

Detail: `docs/design/2026-10-12-import-design.md`.

## Consequences

- The review decisions of the first import measure how often file-backed candidates are right; that
  figure decides whether any import may admit without review.
- Withdrawal of an admitted entry or batch must exist before any automatic admission or production
  import.
- Two schema checks change (`evidence.kind`, `review_item.reason`); existing vaults need
  `val3 vault upgrade`.
