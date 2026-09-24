# Product variant labels

Use this when a Wearhongxiu product's colour variant name is wrong, unreadable,
or a bare merchant code. The typical case is a 1688 colour code such as `9007`
or a literal source label such as `图片色` reaching the storefront.

## Why WordPress alone is not enough

The published label is generated from the captured 1688 SKU options by the
collector, on every translation refresh and every publication. Correcting the
WordPress product by hand fixes the storefront only until the next capture,
translation refresh, SKU-swatch repair, or style-number update replays the
source data, which writes the original text straight back.

A durable fix therefore has two halves, and both are required:

1. **Collector override** — `product_detail_option_overrides` records
   `source option text -> display name` for one product and dimension. The
   collector applies it while it assembles the WordPress payload, so preview,
   publish, bulk synchronization, and the repair scripts all emit the corrected
   name. The captured text is never rewritten: `source_options` keeps the exact
   1688 value as provenance.
2. **WordPress write** — the corrected label is written into the live product
   through the authenticated `hx/v1/products/sync` route, so the storefront is
   right immediately instead of waiting for the next publication.

`scripts/variant_rename.py` does both. It never re-uploads media: the sync
payload carries the resolver's `colors` and `sku_matrix` (so every swatch keeps
its own `image_id`), and it omits categories, tags, and images so the live
terms, gallery, and featured image are untouched.

## The workflow

Read-only inspection first. It resolves the WordPress product, the collector
source, the captured source text, the published labels, and any stored
overrides:

```powershell
python scripts/variant_rename.py inspect SKG166
python scripts/variant_rename.py inspect https://wearhongxiu.com/product/example-product/
python scripts/variant_rename.py inspect 35278
```

A colour can be named by its published label, by the translated option, or by
the raw captured 1688 text; `inspect` lists every accepted spelling under
`matched_on`. Prefer the captured text: it is the stable identifier, while the
published label is exactly what may change.

Preview the change, then apply it:

```powershell
python scripts/variant_rename.py apply SKG166 --source 9007 --label "Tropical Palm Print"
python scripts/variant_rename.py apply SKG166 --source 9007 --label "Tropical Palm Print" --apply
```

`apply` without `--apply` is a dry run and writes nothing. With `--apply` it:

1. saves the collector override (idempotent: re-saving the same pair updates it);
2. writes the corrected labels into the live WordPress product;
3. reads the product back and prints the stored labels;
4. cross-checks the collector's own `wordpress/preview` payload against what
   WordPress now holds, and fails loudly if the two disagree — that is the proof
   that a later capture cannot revert the name.

`--note "..."` records why the name was chosen. It is stored with the override
and is the only place that reasoning survives. `--purge-cache` purges the
Wearhongxiu cache afterwards; it is optional because the corrected labels
usually appear without it.

To undo a rename, delete the override and restore the captured label:

```powershell
python scripts/variant_rename.py revert SKG166 --source 9007
python scripts/variant_rename.py revert SKG166 --source 9007 --apply
```

`revert` asks the collector what it would now publish and writes that back, so
the collector stays the single source of truth. Removing an override never needs
a code change.

## Choosing a good label

- Name the visible product, not the merchant's internal code. Judge it from the
  main image and the other variants, not from the code alone.
- Keep the storefront vocabulary consistent: Title Case, English, no colour
  codes, no source-language text, no SKU-style strings.
- A pattern or print name is fine when the colour is not a solid one
  (`Tropical Palm Print`), but do not invent a colour the image contradicts.
- One override covers one colour of one product. For a code used across several
  products, run the command once per product; do not assume a global rename.

## Rules and limits

- This writes a live WordPress product and a collector override. Only run it
  when the user asked for that variant rename; both halves are mutations.
- The rename is by exact product. The command refuses an unknown identifier, a
  product that is not collector-managed, and a `--source` that does not match a
  colour on that product; it never renames by title search.
- Only the colour dimension is handled. Other dimensions keep the size and
  option data exactly as captured.
- Collector access needs a bearer key. It is read, in order, from
  `HONGXIU_COLLECTOR_API_KEY` / `COLLECTOR_API_KEY` (and the matching
  `*_API_URL` name), from this skill's `config.env`, or — last — from the
  collector application's Coolify environment. On the worker images the
  harness strips `KEY`/`TOKEN`-shaped variables from shell commands, so the
  values come from the skill credential file through `skill_credentials.py`;
  the same script works in both places. The key is sent only to a
  `*.yiswim.cloud` host.
- Never print the key. The command reports only identifiers, labels, and HTTP
  status.
- After a successful apply, if the user wants the change visible immediately and
  the storefront still serves the old label, purge the cache with `--purge-cache`
  or `python scripts/hostinger.py cache-purge`.
