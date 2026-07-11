# CLAUDE.md

## Docstrings

- Always use NumPy-style docstrings
  (`Parameters` / `Returns` / `Raises` sections with the underlined-header format).

## Pandas DataFrame Core Rules

### Use domain wrappers

**Pattern**
- One wrapper class per DF "type" (e.g., `Orders`, `Entries`, `DailyKpis`).
- The wrapper class owns the contract: **schema + grain + guarantees**.
- Construct wrappers only via a validated `from_df(df)` (strict) constructor.
- Wrapper surface area:
  - `.df` (underlying DataFrame; treat as read-only in shared code)
  - metadata: `.grain`, `.keys`, `.schema_name`
  - domain methods for common transforms (avoid free-form mutation outside)

**Rules**
- Functions should accept/return wrappers (not raw `pd.DataFrame`) for domain concepts.
- Inside functions, it's OK to unwrap to `.df` for pandas ops—return a wrapper again.
- Avoid in-place mutation of `.df` in shared/app code; prefer returning a new wrapper.

### Validate at boundaries (ingress/egress + major transforms)
Validate schema + guarantees:
- after reading external data (DB/files/APIs)
- before/after joins
- at entry to business-critical functions (unless wrapper construction guarantees it)
- before writing/publishing

Validation must check (at minimum):
- required columns present
- dtypes as expected
- key columns: no unexpected nulls
- grain key uniqueness (if required)
- category/value constraints (when relevant)

Fail fast with clear error messages.

### Prefer explicit, small transforms (predictable outputs)
- Keep transforms small, named, and single-purpose.
- Functions must return predictable DF “types” (wrappers), not “whatever columns happen to exist.”
- Avoid in-place mutation in application/shared code; prefer `.assign(...)`, `.pipe(...)`, and returning new objects.

### Standardize joins (schema drift hot-spot)
- Every `merge` must specify `how=` and join keys explicitly (`on=` or `left_on/right_on`).
- Set `validate=` (`one_to_one`, `one_to_many`, etc.) whenever possible.
- Control suffixes explicitly and rename columns back to canonical names.
- After merge, validate:
  - row count sanity (if expected)
  - grain key uniqueness (if required)
  - no unexpected nulls in keys

### Use column sets instead of ad-hoc strings
- Use predefined `KEY_COLS`, `DIM_COLS`, `FACT_COLS` (and other groups) for selects, merges, and outputs.
- Avoid copying/pasting raw column name lists across modules.

### Log compact schema diagnostics at key points
At major pipeline steps, log:
- `df.shape`
- key column null counts
- a compact schema summary (columns + dtypes)