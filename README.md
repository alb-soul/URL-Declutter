# url-declutter

Smart URL list declutter for bug bounty & recon.

Recon tooling (waybackurls, gau, katana, crawls) dumps thousands of URLs that
are really the same **template** (locale prefixes, product IDs, pagination,
utm params, sitemaps, static assets…). `url-declutter` collapses that noise
into a compact, human-reviewable list while keeping structurally unique URLs.

```bash
$ cat urls.txt | python3 url-declutter.py --keep 1 --stats
https://wolt.com/en/discovery/category/restaurant/12345
https://wolt.com/en/about
https://cdn.wolt.com/food/sitemap.xml
Original : 5000
Kept     :  412
Removed  : 4588
```

## Features

- **Default heuristic mode** — domain-agnostic rules that work on any target:
  locale prefixes (`/en/about`, `/de/about`), opaque IDs/UUIDs/hashes,
  category/listing patterns, pagination & tracking query noise, sitemaps,
  static assets.
- **Config mode** — bring your own regex rules (YAML) for domain-specific
  cleanup; combinable with the built-in rules or used alone.
- **`--keep N`** — keep at most `N` samples per noisy group (per-rule override
  supported in YAML).
- **`--list-groups`** — debug view of every group and its size before deciding.
- Zero dependencies in default mode (Python 3.8+). `PyYAML` only needed for
  `--config`.

## Install / run

Single self-contained script — no install required:

```bash
# direct
python3 url-declutter.py urls.txt -o cleaned.txt --stats

# or make a global `url-declutter` command (symlink)
chmod +x url-declutter.py
mkdir -p ~/bin && ln -sf "$(pwd)/url-declutter.py" ~/bin/url-declutter
# ensure ~/bin is on PATH
export PATH="$HOME/bin:$PATH"
url-declutter urls.txt -o cleaned.txt --stats
```

Dependencies:

| Mode | Extra packages |
|---|---|
| Default | none |
| `--config` | `pip install pyyaml` |

## Usage

```bash
# collapse a list to stdout, keep 2 samples per noisy group
python3 url-declutter.py urls.txt

# write output, print stats
python3 url-declutter.py urls.txt -o cleaned.txt --stats

# keep 1 sample per group (tightest de-dupe)
python3 url-declutter.py urls.txt --keep 1 -o cleaned.txt

# custom YAML rules on top of the built-in heuristics
python3 url-declutter.py urls.txt --config rules.example.yaml -o cleaned.txt --stats

# only your rules, ignore built-ins
python3 url-declutter.py urls.txt --config rules.example.yaml --no-default -o cleaned.txt

# pipe stdin → stdout
cat urls.txt | url-declutter --keep 2 > cleaned.txt

# debug: show every group and its size
python3 url-declutter.py urls.txt --list-groups | head
```

### CLI flags

```
input                 Input file (one URL per line). "-" = stdin (default)
-o, --output          Output file. Default: stdout
-k, --keep N          Max samples kept per noisy group (default: 2)
-c, --config FILE     YAML config → enable configurable mode
--no-default          Disable built-in heuristic rules (config only)
--stats               Print summary to stderr
--list-groups         Print group key + size then exit (debug)
```

## Default heuristic groups

URLs are bucketed by an ordered list of rules; first match wins. Pages that
don't match any pattern are kept as **structurally unique**.

| Group | Matches | Example |
|---|---|---|
| `sitemap` | `*sitemap*.xml` | `cdn.example.com/sitemap_index.xml` |
| `static-assets` | `/assets/, /static/, /_next/, /dist/, /media/…` | `…/_next/static/chunks/…` |
| `static-file` | `.css/.js/.png/.svg/.woff2…` | `…/app.min.js` |
| `shortlink` | short opaque single segment | `example.com/x7KpQ` |
| `locale-page` | leading locale code | `/en/about` & `/de/about` |
| `id-path` | UUID / long hex / numeric id in path | `/product/12345`, `/post/a1b2c3d4-…` |
| `listing` | category/tag/brand/collection patterns | `/category/restaurant` |
| `path-with-noise-qs` | utm/pagination/tracking params | `?utm_source=…&page=2` |
| `template` | fallback: locale/id segments normalized | `/user/{id}/settings` |
| `UNIQUE` | everything else, kept as-is | `/about`, `/docs/api` |

## YAML config (`--config`)

```yaml
keep: 2

rules:
  - name: sitemaps
    match: '.*/.*sitemap.*\.xml'
    group: 'sitemap'

  - name: shortlinks
    match: 'https?://[^/]+/[A-Za-z0-9_-]{4,12}$'
    group: 'shortlink'
    keep: 1

  - name: category
    match: '/(?:category|categories|cat|tag|tags|brand|brands|collection)/([^/?]+)'
    group: 'listing:{1}'          # {1} = first capture group

  - name: detail-id
    match: '/(?:product|item|p|post|article|user|u)/([0-9a-fA-F-]{6,})'
    group: 'detail-id'

  - name: utm-noise
    match: '\?(?:.*(?:utm_|fbclid|gclid|page=|sort=))'
    group: 'query-noise'
```

- `match` — Python regex, searched against the full URL.
- `group` — group key; supports `{1}`, `{2}`, … substituted from capture groups.
- `keep` — optional, overrides the global `--keep` for that rule.

See `rules.example.yaml` for a ready-to-edit template.

## Exit codes

- `0` — success
- `1` — error (input file not found, PyYAML missing with `--config`, …)

## Notes / limits

- Heuristics are intentionally conservative: ambiguous pages stay unique rather
  than being wrongly collapsed. Tune with `--config` for a known target.
- Grouping is structural (URL shape), not semantic — it does not fetch pages.
- `--keep` keeps the **first** `N` occurrences in input order.

MIT.
