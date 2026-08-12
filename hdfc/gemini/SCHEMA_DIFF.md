## Schema diff — converted Gemini schema vs repo `GT_SCHEMA`

- Gemini leaf fields: **26**
- GT_SCHEMA leaf fields: **32**
- Shared: **26**

### Present in GT_SCHEMA, ABSENT from the Gemini schema

| # | leaf path | GT_SCHEMA type |
|---|---|---|
| 1 | `cards.bigPicture.cardAvailableCreditLimit` | number |
| 2 | `cards.bigPicture.cardCreditLimit` | number |
| 3 | `rewards.bonusPointsThisCycle` | number |
| 4 | `statementMeta.rawStatementId` | string |
| 5 | `statementMeta.statementPeriodEnd` | string |
| 6 | `statementMeta.statementPeriodStart` | string |

### Present in the Gemini schema, ABSENT from GT_SCHEMA

**None.** The Gemini schema is a strict SUBSET of GT_SCHEMA's leaf set.

### Shared leaf, DIFFERENT constraint

| leaf path | Gemini | GT_SCHEMA |
|---|---|---|
| `transactions.direction` | string | string enum[3] |
| `transactions.txnType` | string | string enum[12] |

### `statementLevelSummary.utilisationPercent`

- in converted Gemini schema: **False**
- in repo `GT_SCHEMA`: **False**

