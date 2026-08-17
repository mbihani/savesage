# Schema provenance

## Shared GT schema (`gt_schema.json`)

Vendored at repository commit `26f27bc53910c0e5c97cf7c096ae9c3bd505c4db`.

`hdfc/hdfc_lib.py:61-64` defines the strict response format and points its schema
member to `G.GT_SCHEMA`. In this commit `G` is an external checkout import, so the
schema is not literally inlined in that file. The repository-owned, structurally
identical canonical definition was copied from `sbi/gt298_lib.py:131-231` (the
`_s` helper, `TXN_TYPES`, and `GT_SCHEMA`). The drift test first verifies HDFC's
reference and then evaluates that canonical definition with a restricted AST
interpreter; it never imports repository modules.

`gt_schema.json` stays the **default/fallback** schema: `load_gt_schema()` and
the `schema=None` default on `validate_payload` / `validate_schema_conformance`
both resolve to it for back-compat. The per-bank schemas below are the live
extraction/validation path; `gt_schema.json` is the structural floor they are
all reconciled against.

## Per-bank schemas

Each bank has a dedicated schema under `schema/<bank>.json`, wired by detected
bank via `rules.routing.SCHEMA_BY_BANK` (mirroring `PROMPT_BY_BANK`). They are
the schema sent to the model (`harness.extraction_adapter`) and the schema the
validation node checks against (`graph.validation.validate_payload`).

### Reconcile rule (decision C)

`schema/hdfc.json`, `schema/icici.json`, and `schema/sbi.json` are each a
**structural superset** of `schema/gt_schema.json`, reconciled from the matching
`<bank>/gemini/GEMINI_SCHEMA.json`:

- They do NOT drop any field or any `required` entry that `gt_schema.json` has,
  anywhere in the tree. In particular `statementMeta.rawStatementId`,
  `statementPeriodStart`, `statementPeriodEnd`, `cards[].bigPicture`, and
  `rewards.bonusPointsThisCycle` — all absent from the GEMINI sources — are
  present and required exactly as `gt_schema.json` requires them.
- They do NOT tighten validation beyond `gt_schema.json` in any way that would
  reject a payload `gt_schema.json` currently accepts: no new/narrower enum
  restrictions or type narrowings, and `additionalProperties: false` is kept
  consistent with `gt_schema.json`.
- The bank-specific `description` strings from the GEMINI schema are layered
  onto the matching fields. These are **advisory only** (the extraction model
  reads them for guidance) and are the whole point of the per-bank split.

Practically: each per-bank schema starts from `gt_schema.json`'s structure and
constraints as the floor, with the GEMINI `description` text overlaid onto the
corresponding fields. The `tests/test_schema_per_bank.py` SUPERSET GATE asserts
this property (every field path and every `required` entry in `gt_schema.json`
is present in each per-bank schema) so a future edit that drops a GT field fails
the test gate rather than silently breaking persistence/judge downstream.

### Source commits

| Bank   | GEMINI source file             | Reconciled from commit                           |
|--------|--------------------------------|--------------------------------------------------|
| HDFC   | `hdfc/gemini/GEMINI_SCHEMA.json` | `d266709f10520eee2a18d96efc03e2dff01278b9` |
| ICICI  | `icici/gemini/GEMINI_SCHEMA.json` | `26f27bc53910c0e5c97cf7c096ae9c3bd505c4db` |
| SBI    | `sbi/gemini/GEMINI_SCHEMA.json`  | `f2301159945f41dbcedc030fe2b828e352c31190`  |

(There is no AXIS GEMINI source; see below.)

### AXIS (decision B1)

`schema/axis.json` is an **exact copy** of `schema/gt_schema.json` (json-equal
and byte-identical). AXIS reuses the generic shared schema — there is no
bank-specific extraction guidance to carry, so the generic GT schema is the
correct schema for AXIS extractions. `tests/test_schema_per_bank.py` asserts
axis.json is json-equal to gt_schema.json.
