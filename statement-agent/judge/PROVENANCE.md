# Scoring provenance

The normalization and matching discipline was ported from repository commit
`b2cf196adb04ff15f5304297232adfe3b53eaaf8` (the last commit touching all three
bank scorers at the implementation base).

- `hdfc/score_lib.py:29-110`: `norm_date`, `norm_num`, and `norm_desc`.
- `hdfc/score_lib.py:238-307`: description similarity and strict greedy 1:1,
  order-insensitive transaction assignment, including the equal-similarity
  positional tie-break. `hdfc/score_lib.py:263` fixes the HDFC threshold at 0.55.
- `icici/score_lib.py:35`: ICICI imports canonical `text` and `num`; it does not
  define similarly named local normalizers. Its matcher uses threshold 0.60.
- `icici/score_lib.py:177-183`: trailing-digit `norm4` behavior.
- `icici/score_lib.py:287-318`: ICICI description-only 1:1 matcher and 0.60
  threshold.
- `sbi/score_lib_sbi.py:34` and `:49-63`: canonical imports and SBI's local date
  wrapper. The seven-field judge deliberately uses the HDFC DD/MM/YYYY port named
  by the frozen contract rather than copying bank-specific wrappers.
- `sbi/score_lib_sbi.py:329-362`: SBI description-only matcher and 0.60 threshold.
- `/Users/mayanck.bihani/Savesage/bakeoff/scorer/score.py:123-134`: canonical
  ICICI/SBI `text`, `num`, and `date_norm` implementation inspected for parity.

No legacy scorer file was edited.
