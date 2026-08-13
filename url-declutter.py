#!/usr/bin/env python3
"""
url-declutter — Smart URL list declutter tool (general purpose)

Default mode  : generic heuristic rules (works on any target)
Config mode   : --config rules.yaml  → custom / domain-specific rules

Usage:
  url-declutter input.txt -o cleaned.txt
  url-declutter input.txt --keep 2 --stats
  url-declutter input.txt --config rules.yaml -o out.txt
  cat urls.txt | url-declutter --keep 3 > cleaned.txt
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse, unquote

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GroupKey = str
RuleFunc = Callable[[str], Optional[GroupKey]]

# Common locale / language path segments (2–5 letter codes + common variants)
_LOCALE_RE = re.compile(
    r"^(?:"
    r"[a-z]{2}"                                  # en, de, fr, id, ...
    r"(?:-[a-z]{2,8})?"                          # en-us, zh-hans, pt-br
    r"|[a-z]{2,3}_[a-z]{2}"                      # en_US style
    r")$",
    re.I,
)

# Looks like an opaque id / hash / uuid / numeric id
_ID_RE = re.compile(
    r"^(?:"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"  # uuid
    r"|[0-9a-f]{16,}"                                                    # long hex
    r"|[0-9a-f]{24}"                                                     # mongo-like
    r"|[A-Za-z0-9_-]{20,}"                                               # long token
    r"|\d{5,}"                                                           # long numeric
    r")$",
    re.I,
)

# Static file / asset extensions
_ASSET_EXT_RE = re.compile(
    r"\.(?:css|js|mjs|map|png|jpe?g|gif|svg|webp|ico|woff2?|ttf|eot|mp4|webm|mp3|pdf|xml|txt|csv)(?:\?.*)?$",
    re.I,
)


def _segs(path: str) -> List[str]:
    return [s for s in path.strip("/").split("/") if s]


def _normalize_path_for_template(path: str) -> str:
    """
    Replace variable-looking segments with placeholders so similar
    template URLs collapse into the same group.
    """
    segs = _segs(path)
    out = []
    for s in segs:
        if _LOCALE_RE.match(s):
            out.append("{locale}")
        elif _ID_RE.match(s):
            out.append("{id}")
        elif s.isdigit() and len(s) >= 3:
            out.append("{num}")
        else:
            out.append(s)
    return "/" + "/".join(out)


# ---------------------------------------------------------------------------
# Default generic rules (domain-agnostic)
# ---------------------------------------------------------------------------

def default_rules() -> List[RuleFunc]:
    """
    Ordered list of generic rule functions.
    First non-None key wins.
    These rules intentionally contain NO hard-coded target domains.
    """

    def rule_sitemap(url: str) -> Optional[str]:
        p = urlparse(url)
        path = p.path.lower()
        if path.endswith("sitemap.xml") or path.endswith("sitemap_index.xml") or "sitemap" in path and path.endswith(".xml"):
            return f"{p.netloc}:sitemap"
        return None

    def rule_assets_path(url: str) -> Optional[str]:
        p = urlparse(url)
        path = p.path
        # /assets/, /static/, /_next/, /dist/, /build/, /media/, /uploads/ (shallow)
        if re.search(r"/(?:assets|static|_next|dist|build|media|chunks|scripts|styles)(/|$)", path, re.I):
            return f"{p.netloc}:static-assets"
        if _ASSET_EXT_RE.search(path):
            return f"{p.netloc}:static-file"
        return None

    def rule_short_opaque(url: str) -> Optional[str]:
        """Short-link / opaque code style: domain + single short segment."""
        p = urlparse(url)
        segs = _segs(p.path)
        if len(segs) == 1 and 3 <= len(segs[0]) <= 12 and re.match(r"^[A-Za-z0-9_-]+$", segs[0]):
            # avoid collapsing normal pages like /login, /about
            if segs[0].lower() not in {
                "login", "logout", "signin", "signup", "register", "about",
                "contact", "help", "support", "home", "index", "search",
                "cart", "checkout", "account", "profile", "settings", "admin",
                "api", "docs", "blog", "news", "faq", "terms", "privacy",
            }:
                return f"{p.netloc}:shortlink"
        return None

    def rule_locale_prefix(url: str) -> Optional[str]:
        """
        Collapse pages that differ only by leading locale:
          /en/about  /de/about  /fr/about  → same group
        """
        p = urlparse(url)
        segs = _segs(p.path)
        if not segs:
            return None
        if _LOCALE_RE.match(segs[0]) and len(segs) >= 2:
            rest = "/".join(segs[1:])
            # further normalize ids in the rest
            rest_norm = _normalize_path_for_template("/" + rest).lstrip("/")
            return f"{p.netloc}:locale-page:/{rest_norm}"
        return None

    def rule_id_in_path(url: str) -> Optional[str]:
        """
        Paths containing obvious IDs / UUIDs / long tokens:
          /product/12345  /user/a1b2c3d4-...  /post/9f3a...
        """
        p = urlparse(url)
        segs = _segs(p.path)
        if not segs:
            return None
        if any(_ID_RE.match(s) or (s.isdigit() and len(s) >= 5) for s in segs):
            norm = _normalize_path_for_template(p.path)
            return f"{p.netloc}:id-path:{norm}"
        return None

    def rule_category_like(url: str) -> Optional[str]:
        """
        Common listing patterns:
          /category/xxx  /categories/xxx  /cat/xxx
          /tag/xxx  /tags/xxx
          /brand/xxx  /brands/xxx
          /collection/xxx
          /topic/xxx  /topics/xxx
          /genre/xxx
        Keep the *pattern*, collapse the variable last segment.
        """
        p = urlparse(url)
        segs = _segs(p.path)
        if len(segs) < 2:
            return None
        # find a known listing keyword
        keywords = {
            "category", "categories", "cat", "tag", "tags",
            "brand", "brands", "collection", "collections",
            "topic", "topics", "genre", "genres", "section",
            "department", "type", "types", "filter",
        }
        for i, s in enumerate(segs[:-1]):
            if s.lower() in keywords:
                # group by everything up to and including the keyword
                prefix = "/".join(segs[: i + 1])
                return f"{p.netloc}:listing:/{prefix}/{{item}}"
        return None

    def rule_pagination_or_query_noise(url: str) -> Optional[str]:
        """
        Same path, different boring query params (page, sort, utm, ref, ...).
        Group by path only when query looks like tracking/pagination.
        """
        p = urlparse(url)
        if not p.query:
            return None
        q = p.query.lower()
        noise_keys = (
            "utm_", "fbclid", "gclid", "ref=", "referrer", "source=",
            "page=", "p=", "sort=", "order=", "limit=", "offset=",
            "session", "sid=", "amp=", "mc_", "campaign",
        )
        if any(k in q for k in noise_keys):
            norm = _normalize_path_for_template(p.path)
            return f"{p.netloc}:path-with-noise-qs:{norm}"
        return None

    def rule_deep_similar_template(url: str) -> Optional[str]:
        """
        Fallback template detection:
        normalize locale + id segments, group by resulting shape.
        Only collapses when the normalized form still has at least one placeholder
        (otherwise it would be a unique page).
        """
        p = urlparse(url)
        if not p.path or p.path == "/":
            return None
        norm = _normalize_path_for_template(p.path)
        if "{locale}" in norm or "{id}" in norm or "{num}" in norm:
            return f"{p.netloc}:template:{norm}"
        return None

    return [
        rule_sitemap,
        rule_assets_path,
        rule_short_opaque,
        rule_locale_prefix,
        rule_category_like,
        rule_id_in_path,
        rule_pagination_or_query_noise,
        rule_deep_similar_template,
    ]


def apply_default_rules(url: str, rules: List[RuleFunc]) -> str:
    for rule in rules:
        key = rule(url)
        if key is not None:
            return key
    # truly unique
    p = urlparse(url)
    return f"UNIQUE:{p.netloc}{p.path.rstrip('/')}"


# ---------------------------------------------------------------------------
# Configurable rules (mode 2)
# ---------------------------------------------------------------------------

def load_config_rules(config_path: Path) -> Tuple[List[dict], int]:
    """
    Load YAML config.

    Format:

      keep: 2

      rules:
        - name: sitemaps
          match: 'example\\.com/.*sitemap.*\\.xml'
          group: 'sitemap'
        - name: categories
          match: 'example\\.com/category/([^/?]+)'
          group: 'cat:{1}'
          keep: 2
    """
    try:
        import yaml
    except ImportError:
        print(
            "ERROR: PyYAML required for --config.\n"
            "  Install:  pip install pyyaml\n"
            "       or:  pacman -S python-yaml   (Arch)\n"
            "       or:  apt install python3-yaml (Debian/Ubuntu)",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    rules = data.get("rules", [])
    default_keep = int(data.get("keep", 2))
    return rules, default_keep


def apply_config_rules(url: str, config_rules: List[dict]) -> Optional[str]:
    for rule in config_rules:
        pattern = rule.get("match", "")
        if not pattern:
            continue
        m = re.search(pattern, url)
        if m:
            group_tpl = rule.get("group", rule.get("name", "custom"))
            for i, val in enumerate(m.groups(), start=1):
                group_tpl = group_tpl.replace(f"{{{i}}}", val or "")
            return group_tpl
    return None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def declutter(
    urls: List[str],
    keep: int = 2,
    use_default: bool = True,
    config_rules: Optional[List[dict]] = None,
) -> Tuple[List[str], dict]:
    rules = default_rules() if use_default else []
    config_rules = config_rules or []

    groups: Dict[str, List[str]] = defaultdict(list)
    group_keep: Dict[str, int] = {}

    # pre-index per-rule keep from config
    for rule in config_rules:
        if "keep" in rule and "group" in rule:
            # approximate: store by group template prefix
            group_keep[str(rule["group"]).split("{")[0]] = int(rule["keep"])

    for url in urls:
        key: Optional[str] = None

        if config_rules:
            key = apply_config_rules(url, config_rules)

        if key is None and use_default:
            key = apply_default_rules(url, rules)

        if key is None:
            p = urlparse(url)
            key = f"UNIQUE:{p.netloc}{p.path.rstrip('/')}"

        groups[key].append(url)

    kept: List[str] = []
    stats = {
        "original": len(urls),
        "kept": 0,
        "removed": 0,
        "groups_total": len(groups),
        "groups_collapsed": 0,
        "unique_structural": 0,
    }

    url_to_idx = {u: i for i, u in enumerate(urls)}

    for key, members in groups.items():
        # resolve keep limit
        limit = keep
        for prefix, k in group_keep.items():
            if key.startswith(prefix):
                limit = k
                break

        if key.startswith("UNIQUE:"):
            take = members
            stats["unique_structural"] += len(members)
        else:
            take = members[:limit]
            if len(members) > limit:
                stats["groups_collapsed"] += 1

        kept.extend(take)

    kept = sorted(set(kept), key=lambda u: url_to_idx.get(u, 10**9))
    stats["kept"] = len(kept)
    stats["removed"] = stats["original"] - stats["kept"]
    return kept, stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="url-declutter",
        description="Smart URL list declutter — collapse template-like URLs, keep unique ones.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  url-declutter urls.txt -o cleaned.txt --stats
  url-declutter urls.txt --keep 3 --stats
  url-declutter urls.txt --config rules.yaml -o out.txt
  cat urls.txt | url-declutter --keep 2 > cleaned.txt

Default mode uses generic heuristics (locale, ids, categories, sitemaps, assets...).
Use --config for domain-specific rules (recommended for best results on a known target).
""",
    )
    p.add_argument("input", nargs="?", default="-", help="Input file (one URL per line). '-' = stdin.")
    p.add_argument("-o", "--output", default="-", help="Output file. Default: stdout.")
    p.add_argument("-k", "--keep", type=int, default=2, help="Max samples per noisy group (default: 2).")
    p.add_argument("-c", "--config", type=str, default=None, help="YAML config for custom rules.")
    p.add_argument("--no-default", action="store_true", help="Disable built-in generic rules (config only).")
    p.add_argument("--stats", action="store_true", help="Print summary to stderr.")
    p.add_argument("--list-groups", action="store_true", help="Print group keys + sizes then exit.")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.input == "-":
        raw = sys.stdin.read()
    else:
        path = Path(args.input)
        if not path.exists():
            print(f"ERROR: input file not found: {args.input}", file=sys.stderr)
            return 1
        raw = path.read_text(encoding="utf-8", errors="replace")

    urls = [ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if not urls:
        print("WARNING: no URLs found", file=sys.stderr)
        return 0

    config_rules: List[dict] = []
    use_default = not args.no_default
    keep = args.keep

    if args.config:
        cfg = Path(args.config)
        if not cfg.exists():
            print(f"ERROR: config not found: {args.config}", file=sys.stderr)
            return 1
        config_rules, yaml_keep = load_config_rules(cfg)
        # CLI -k always wins; yaml keep only if user left default? keep CLI.

    if args.list_groups:
        rules = default_rules() if use_default else []
        groups: Dict[str, List[str]] = defaultdict(list)
        for u in urls:
            key = None
            if config_rules:
                key = apply_config_rules(u, config_rules)
            if key is None and use_default:
                key = apply_default_rules(u, rules)
            if key is None:
                p = urlparse(u)
                key = f"UNIQUE:{p.netloc}{p.path.rstrip('/')}"
            groups[key].append(u)
        for k, members in sorted(groups.items(), key=lambda x: -len(x[1])):
            print(f"{len(members):5d}  {k}")
        return 0

    kept, stats = declutter(
        urls,
        keep=keep,
        use_default=use_default,
        config_rules=config_rules or None,
    )

    out_text = "\n".join(kept) + ("\n" if kept else "")
    if args.output == "-":
        sys.stdout.write(out_text)
    else:
        Path(args.output).write_text(out_text, encoding="utf-8")
        if args.stats:
            print(f"Wrote {stats['kept']} URLs → {args.output}", file=sys.stderr)

    if args.stats:
        print(
            f"Original : {stats['original']}\n"
            f"Kept     : {stats['kept']}\n"
            f"Removed  : {stats['removed']}\n"
            f"Groups   : {stats['groups_total']} total, "
            f"{stats['groups_collapsed']} collapsed, "
            f"{stats['unique_structural']} unique-structural",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
