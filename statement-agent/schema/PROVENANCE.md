# GT schema provenance

Vendored at repository commit `26f27bc53910c0e5c97cf7c096ae9c3bd505c4db`.

`hdfc/hdfc_lib.py:61-64` defines the strict response format and points its schema
member to `G.GT_SCHEMA`. In this commit `G` is an external checkout import, so the
schema is not literally inlined in that file. The repository-owned, structurally
identical canonical definition was copied from `sbi/gt298_lib.py:131-231` (the
`_s` helper, `TXN_TYPES`, and `GT_SCHEMA`). The drift test first verifies HDFC's
reference and then evaluates that canonical definition with a restricted AST
interpreter; it never imports repository modules.
