#!/usr/bin/env python3
"""arXiv daily digest.

Queries the arXiv API for recent papers matching the topics in config.json,
drops anything already seen on a previous run, and writes a dated Markdown
digest into digests/.

Standard library only: nothing to pip install, nothing to pin, nothing to rot.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

API_URL = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
USER_AGENT = "daily-automations-arxiv-digest/1.0"

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
STATE_PATH = HERE / "state" / "seen.json"
DIGEST_DIR = HERE / "digests"
MAX_SEEN = 5000
VERSION_SUFFIX = re.compile(r"v\d+$")


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


# ------------------------------------------------------------------- arxiv api

def http_get(url: str, retries: int = 4, timeout: int = 60) -> bytes:
    """GET a URL, backing off when arXiv throttles us.

    arXiv answers bursts with 429 "Rate exceeded", sometimes per shared IP
    rather than per client, so the retries here are deliberately patient.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if attempt == retries or exc.code not in (429, 500, 502, 503, 504):
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                wait = int(retry_after)
            except (TypeError, ValueError):
                wait = 20 * attempt
            print(
                "  ! HTTP %s from arXiv; waiting %ss" % (exc.code, wait),
                file=sys.stderr,
            )
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == retries:
                raise
            wait = 5 * attempt
            print("  ! request failed (%s); retry in %ss" % (exc, wait), file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def build_query(phrases: list, since: datetime, until: datetime) -> str:
    """Phrase search, bounded to a submission window.

    The date range is part of the query rather than a filter applied to the
    results: an unbounded phrase search sorted by submission date is slow enough
    on arXiv's side to time out, and it would also spend the result budget on
    papers too old to report.
    """
    joined = " OR ".join('all:"%s"' % phrase for phrase in phrases)
    window = "submittedDate:[%s TO %s]" % (
        since.strftime("%Y%m%d%H%M"),
        until.strftime("%Y%m%d%H%M"),
    )
    return "(" + joined + ") AND " + window


def squeeze(text) -> str:
    return " ".join((text or "").split())


def parse_entries(payload: bytes) -> list:
    root = ET.fromstring(payload)
    entries = []
    for node in root.findall(ATOM + "entry"):
        raw_id = squeeze(node.findtext(ATOM + "id"))
        if not raw_id:
            continue
        versioned = raw_id.rsplit("/abs/", 1)[-1]
        entries.append(
            {
                "id": VERSION_SUFFIX.sub("", versioned),
                "url": raw_id.replace("http://", "https://", 1),
                "title": squeeze(node.findtext(ATOM + "title")),
                "abstract": squeeze(node.findtext(ATOM + "summary")),
                "published": squeeze(node.findtext(ATOM + "published")),
                "authors": [
                    squeeze(a.findtext(ATOM + "name"))
                    for a in node.findall(ATOM + "author")
                ],
                "categories": [
                    c.get("term", "") for c in node.findall(ATOM + "category")
                ],
            }
        )
    return entries


def published_date(entry: dict):
    try:
        stamp = datetime.strptime(entry["published"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return stamp.replace(tzinfo=timezone.utc)


def collect(config: dict, seen: set, lookback_days: int):
    """Query every topic. Returns (papers by topic, labels that failed)."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=lookback_days)
    allowed = set(config.get("categories") or [])
    delay = config.get("request_delay_seconds", 3)
    topics = config["topics"]
    found = {}
    claimed = set()
    failed = []

    for index, topic in enumerate(topics):
        label = topic["label"]
        params = {
            "search_query": build_query(topic["phrases"], cutoff, now),
            "start": 0,
            "max_results": config.get("max_results_per_query", 25),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        url = API_URL + "?" + urllib.parse.urlencode(params)
        print("-> " + label)

        try:
            entries = parse_entries(http_get(url))
        except (urllib.error.URLError, TimeoutError, OSError, ET.ParseError) as exc:
            # One unlucky topic should not cost us the whole digest.
            print("   ! giving up on this topic: %s" % exc, file=sys.stderr)
            failed.append(label)
            if index < len(topics) - 1:
                time.sleep(delay)
            continue

        keep = []
        for entry in entries:
            if entry["id"] in seen or entry["id"] in claimed:
                continue
            stamp = published_date(entry)
            if stamp is None or stamp < cutoff:
                continue
            if allowed and not allowed.intersection(entry["categories"]):
                continue
            keep.append(entry)
            claimed.add(entry["id"])

        print("   %d returned, %d new and in scope" % (len(entries), len(keep)))
        if keep:
            found[label] = keep

        # arXiv asks API clients to pace their requests.
        if index < len(topics) - 1:
            time.sleep(delay)

    if failed:
        print("%d of %d topics failed: %s" % (len(failed), len(topics), ", ".join(failed)))
    return found, failed


# -------------------------------------------------------------------- markdown

def snippet(text: str, limit: int = 300) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(".,;:") + " ..."


def byline(authors: list) -> str:
    if not authors:
        return "_authors not listed_"
    if len(authors) <= 3:
        return ", ".join(authors)
    return ", ".join(authors[:3]) + ", et al. (%d authors)" % len(authors)


def plural(count: int) -> str:
    return "s" if count != 1 else ""


def render_sections(found: dict, failed: list = ()) -> list:
    lines = []
    for label, papers in found.items():
        lines.append("## %s (%d)" % (label, len(papers)))
        lines.append("")
        for paper in papers:
            cats = ", ".join("`%s`" % c for c in paper["categories"][:4])
            lines.append("### [%s](%s)" % (paper["title"], paper["url"]))
            lines.append("%s - %s - %s  " % (paper["id"], paper["published"][:10], cats))
            lines.append(byline(paper["authors"]))
            lines.append("")
            lines.append("> " + snippet(paper["abstract"]))
            lines.append("")

    if failed:
        lines.append("---")
        lines.append("")
        lines.append(
            "_arXiv did not answer for: %s. Those topics will be picked up on the "
            "next run._" % ", ".join(failed)
        )
        lines.append("")
    return lines


def render(found: dict, day: str, failed: list = ()) -> str:
    total = sum(len(papers) for papers in found.values())
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = ["# arXiv digest - " + day, ""]
    if total:
        lines.append(
            "**%d new paper%s** across %d topic%s."
            % (total, plural(total), len(found), plural(len(found)))
        )
    else:
        lines.append("No new papers matched today's topics.")
    lines.append("Generated %s by [arxiv-digest](../README.md)." % generated)
    lines.append("")
    lines += render_sections(found, failed)

    return "\n".join(lines).rstrip() + "\n"


def render_appendix(found: dict, failed: list = ()) -> str:
    """Extra sections for a day whose digest already exists.

    A re-run finds only what the earlier run missed, so it must add to that
    digest rather than replace it with the remainder.
    """
    stamp = datetime.now(timezone.utc).strftime("%H:%M UTC")
    lines = ["", "---", "", "## Added by a later run at " + stamp, ""]
    lines += render_sections(found, failed)
    return "\n".join(lines).rstrip() + "\n"


# ------------------------------------------------------------------------ main

def emit_action_output(count: int) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write("new_papers=%d\n" % count)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build today's arXiv digest.")
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
    lookback = args.lookback or config.get("lookback_days", 14)

    print("%d papers already seen; last run %s" % (len(state["ids"]), state["last_run"]))
    found, failed = collect(config, set(state["ids"]), lookback)
    total = sum(len(papers) for papers in found.values())

    if failed and not found:
        print("Every topic failed; not writing a digest.", file=sys.stderr)
        emit_action_output(0)
        return 1

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    digest = render(found, day, failed)

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
            fh.write(render_appendix(found, failed))
    else:
        path.write_text(digest, encoding="utf-8")

    new_ids = [paper["id"] for papers in found.values() for paper in papers]
    save_seen(state, new_ids)

    print(
        "%s %s with %d paper%s."
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
