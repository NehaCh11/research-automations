#!/usr/bin/env python3
"""Citation watch.

Two things arxiv-digest cannot do, both answered by the OpenAlex API:

1. Follow the citation graph. "What cited the three papers this thesis rests
   on" is not a keyword search, and no keyword list would find it reliably.
2. See the published literature. arXiv carries almost no clinical cardiology,
   and conference proceedings like Interspeech and Computing in Cardiology
   never appear there at all.

Preprints are excluded by default so the two tools never report the same work.

Standard library only: nothing to pip install, nothing to pin, nothing to rot.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API_URL = "https://api.openalex.org/works"
USER_AGENT = "research-automations-citation-watch/1.0"

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
STATE_PATH = HERE / "state" / "seen.json"
DIGEST_DIR = HERE / "digests"
MAX_SEEN = 5000

SELECT_FIELDS = ",".join(
    [
        "id",
        "display_name",
        "publication_date",
        "type",
        "doi",
        "cited_by_count",
        "primary_location",
        "authorships",
        "abstract_inverted_index",
    ]
)


# ---------------------------------------------------------------- config/state

def load_config() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_seen() -> dict:
    if not STATE_PATH.exists():
        return {"ids": [], "last_run": None}
    try:
        with STATE_PATH.open(encoding="utf-8") as fh:
            state = json.load(fh)
    except json.JSONDecodeError:
        print("! seen.json is corrupt; starting from an empty state", file=sys.stderr)
        return {"ids": [], "last_run": None}
    state.setdefault("ids", [])
    state.setdefault("last_run", None)
    return state


def save_seen(state: dict, new_ids: list) -> None:
    ids = list(dict.fromkeys(state["ids"] + new_ids))[-MAX_SEEN:]
    payload = {
        "ids": ids,
        "last_run": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STATE_PATH.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")


# ----------------------------------------------------------------- openalex io

def http_get(url: str, retries: int = 4, timeout: int = 45) -> dict:
    """GET and parse JSON, backing off when OpenAlex throttles us.

    Without a mailto the requests land in OpenAlex's common pool, which returns
    429 readily, so the retries here are deliberately patient.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if attempt == retries or exc.code not in (429, 500, 502, 503, 504):
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                wait = int(retry_after)
            except (TypeError, ValueError):
                wait = 20 * attempt
            print("  ! HTTP %s; waiting %ss" % (exc.code, wait), file=sys.stderr)
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            if attempt == retries:
                raise
            wait = 5 * attempt
            print("  ! request failed (%s); retry in %ss" % (exc, wait), file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def build_url(filters: list, per_page: int) -> str:
    """Assemble an OpenAlex works URL.

    OpenAlex separates filters with commas, so a comma inside a search phrase
    would silently split it into two filters. Phrases are cleaned in
    clean_phrase() before they get here.
    """
    params = {
        "filter": ",".join(filters),
        "per-page": per_page,
        "sort": "publication_date:desc",
        "select": SELECT_FIELDS,
    }
    mailto = os.environ.get("OPENALEX_MAILTO", "").strip()
    if mailto:
        # Opt-in only. The polite pool is faster and far less likely to 429,
        # but it means handing OpenAlex an email address, so this is never
        # committed to the repo: set it as a secret if you want it.
        params["mailto"] = mailto
    return API_URL + "?" + urllib.parse.urlencode(params)


def clean_phrase(phrase: str) -> str:
    return " ".join(phrase.replace(",", " ").split())


def since_filter(lookback_days: int) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    return "from_publication_date:" + cutoff.strftime("%Y-%m-%d")


# -------------------------------------------------------------------- parsing

def abstract_from_index(index) -> str:
    """Rebuild an abstract from OpenAlex's inverted index.

    OpenAlex stores abstracts as {word: [positions]} rather than as text, so
    the words have to be put back in position order.
    """
    if not isinstance(index, dict) or not index:
        return ""
    positions = []
    for word, spots in index.items():
        if not isinstance(spots, list):
            continue
        for spot in spots:
            if isinstance(spot, int):
                positions.append((spot, word))
    positions.sort()
    return " ".join(word for _, word in positions)


def venue_of(work: dict) -> str:
    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    return source.get("display_name") or "no listed venue"


def link_for(work: dict) -> str:
    doi = work.get("doi")
    if doi:
        return doi if doi.startswith("http") else "https://doi.org/" + doi
    return work.get("id") or ""


def short_id(work: dict) -> str:
    return (work.get("id") or "").rsplit("/", 1)[-1]


def title_key(title: str) -> str:
    """A loose key for spotting the same paper indexed twice.

    OpenAlex sometimes holds two work records for one paper, for instance a
    landing page and its PDF, with different ids and DOIs that differ only by
    a suffix. Identity by id alone lets both into the same digest.
    """
    kept = [c.lower() if c.isalnum() else " " for c in (title or "")]
    return " ".join("".join(kept).split())


def parse_work(raw: dict) -> dict:
    return {
        "id": short_id(raw),
        "title": " ".join((raw.get("display_name") or "untitled").split()),
        "date": raw.get("publication_date") or "",
        "type": raw.get("type") or "",
        "venue": venue_of(raw),
        "url": link_for(raw),
        "cited_by": raw.get("cited_by_count") or 0,
        "authors": [
            (a.get("author") or {}).get("display_name", "")
            for a in (raw.get("authorships") or [])
        ],
        "abstract": abstract_from_index(raw.get("abstract_inverted_index")),
    }


# ------------------------------------------------------------------ collecting

def queries_from(config: dict) -> list:
    """Flatten seeds and topics into one list of labelled queries."""
    out = []
    for seed in config.get("seeds", []):
        out.append(
            {
                "label": "Cites: " + seed["label"],
                "filters": ["cites:" + seed["id"]],
                "kind": "seed",
            }
        )
    for topic in config.get("topics", []):
        phrase = clean_phrase(topic["phrase"])
        # Which field to search is per topic, because it decides precision.
        # "data leakage" in an abstract is mostly cybersecurity, where the
        # phrase means something else; restricted to titles it is the
        # methodological sense this thesis cares about.
        field = topic.get("field", "title_and_abstract")
        if field not in ("title", "title_and_abstract", "abstract"):
            field = "title_and_abstract"
        out.append(
            {
                "label": topic["label"],
                "filters": [field + ".search:" + phrase],
                "kind": "topic",
            }
        )
    return out


def collect(config: dict, seen: set, lookback_days: int):
    """Run every query. Returns (works by label, labels that failed)."""
    queries = queries_from(config)
    delay = config.get("request_delay_seconds", 12)
    per_page = config.get("max_results_per_query", 25)
    recent = since_filter(lookback_days)
    drop_preprints = config.get("exclude_preprints", True)

    found = {}
    failed = []
    capped = {}
    claimed = set()
    claimed_titles = set()

    for index, query in enumerate(queries):
        filters = list(query["filters"]) + [recent]
        if drop_preprints:
            filters.append("type:!preprint")
        url = build_url(filters, per_page)
        print("-> " + query["label"])

        try:
            payload = http_get(url)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            # One unlucky query should not cost us the whole digest.
            print("   ! giving up on this query: %s" % exc, file=sys.stderr)
            failed.append(query["label"])
            if index < len(queries) - 1:
                time.sleep(delay)
            continue

        results = payload.get("results") or []
        keep = []
        for raw in results:
            work = parse_work(raw)
            if not work["id"] or work["id"] in seen or work["id"] in claimed:
                continue
            key = title_key(work["title"])
            if key and key in claimed_titles:
                claimed.add(work["id"])
                continue
            keep.append(work)
            claimed.add(work["id"])
            if key:
                claimed_titles.add(key)

        total = (payload.get("meta") or {}).get("count", len(results))
        print("   %d of %d returned, %d new" % (len(results), total, len(keep)))
        if keep:
            found[query["label"]] = keep
        if total > len(results):
            # Say so in the digest. Silently dropping the rest would look
            # identical to there being nothing else.
            capped[query["label"]] = total

        if index < len(queries) - 1:
            time.sleep(delay)

    if failed:
        print("%d of %d queries failed: %s" % (len(failed), len(queries), ", ".join(failed)))
    return found, failed, capped


# -------------------------------------------------------------------- markdown

def snippet(text: str, limit: int = 300) -> str:
    if not text:
        return "_no abstract indexed_"
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(".,;:") + " ..."


def byline(authors: list) -> str:
    authors = [a for a in authors if a]
    if not authors:
        return "_authors not listed_"
    if len(authors) <= 3:
        return ", ".join(authors)
    return ", ".join(authors[:3]) + ", et al. (%d authors)" % len(authors)


def plural(count: int) -> str:
    return "s" if count != 1 else ""


def render_sections(found: dict, failed: list = (), capped: dict = None) -> list:
    capped = capped or {}
    lines = []
    for label, works in found.items():
        lines.append("## %s (%d)" % (label, len(works)))
        lines.append("")
        if label in capped:
            lines.append(
                "_OpenAlex has %d matches in this window and only the newest were "
                "fetched. Tighten the query or raise max_results_per_query._"
                % capped[label]
            )
            lines.append("")
        for work in works:
            lines.append("### [%s](%s)" % (work["title"], work["url"]))
            lines.append(
                "%s | %s | %s | cited by %d  "
                % (work["date"], work["venue"], work["type"], work["cited_by"])
            )
            lines.append(byline(work["authors"]))
            lines.append("")
            lines.append("> " + snippet(work["abstract"]))
            lines.append("")

    if failed:
        lines.append("---")
        lines.append("")
        lines.append(
            "_OpenAlex did not answer for: %s. Those queries will be picked up on "
            "the next run._" % ", ".join(failed)
        )
        lines.append("")
    return lines


def render(found: dict, day: str, failed: list = (), capped: dict = None) -> str:
    total = sum(len(works) for works in found.values())
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = ["# Citation watch - " + day, ""]
    if total:
        lines.append(
            "**%d new work%s** across %d quer%s."
            % (total, plural(total), len(found), "y" if len(found) == 1 else "ies")
        )
    else:
        lines.append("No new works matched today's seeds or topics.")
    lines.append("Generated %s by [citation-watch](../README.md)." % generated)
    lines.append("")
    lines += render_sections(found, failed, capped)
    return "\n".join(lines).rstrip() + "\n"


def render_appendix(found: dict, failed: list = (), capped: dict = None) -> str:
    """Extra sections for a day whose digest already exists."""
    stamp = datetime.now(timezone.utc).strftime("%H:%M UTC")
    lines = ["", "---", "", "## Added by a later run at " + stamp, ""]
    lines += render_sections(found, failed, capped)
    return "\n".join(lines).rstrip() + "\n"


# ------------------------------------------------------------------------ main

def emit_action_output(count: int) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write("new_works=%d\n" % count)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build today's citation watch.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the digest instead of writing files or updating state",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="write a digest even when nothing new was found",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=None,
        help="override how many days back to accept (default: config value)",
    )
    args = parser.parse_args()

    config = load_config()
    state = load_seen()
    lookback = args.lookback or config.get("lookback_days", 30)

    print("%d works already seen; last run %s" % (len(state["ids"]), state["last_run"]))
    if not os.environ.get("OPENALEX_MAILTO", "").strip():
        print("no OPENALEX_MAILTO set; using the slower common pool")

    found, failed, capped = collect(config, set(state["ids"]), lookback)
    total = sum(len(works) for works in found.values())

    if failed and not found:
        print("Every query failed; not writing a digest.", file=sys.stderr)
        emit_action_output(0)
        return 1

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    digest = render(found, day, failed, capped)

    if args.dry_run:
        print("\n--- dry run, nothing written ---\n")
        print(digest)
        return 0

    if not total and not args.allow_empty:
        print("Nothing new today; no digest written.")
        emit_action_output(0)
        return 0

    DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    path = DIGEST_DIR / (day + ".md")
    appended = path.exists()
    if appended:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(render_appendix(found, failed, capped))
    else:
        path.write_text(digest, encoding="utf-8")

    new_ids = [work["id"] for works in found.values() for work in works]
    save_seen(state, new_ids)

    print(
        "%s %s with %d work%s."
        % (
            "Appended to" if appended else "Wrote",
            path.relative_to(HERE.parent),
            total,
            plural(total),
        )
    )
    emit_action_output(total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
