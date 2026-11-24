# arxiv-digest

[![arXiv digest](https://github.com/NehaCh11/research-automations/actions/workflows/arxiv-digest.yml/badge.svg)](https://github.com/NehaCh11/research-automations/actions/workflows/arxiv-digest.yml)

A scheduled job that reads the arXiv API every morning, keeps the papers that
match a set of topics, skips everything it has already reported, and commits a
dated digest to [`digests/`](digests/).

Browsing arXiv listings by hand means re-reading the same titles every day and
still missing things that land in a category you don't watch. This searches by
phrase instead of by category, and remembers what it has shown you.

## How it works

1. Each topic in [`config.json`](config.json) becomes one arXiv API query: its
   phrases OR'd together, bounded to the lookback window, newest first.
2. Results are filtered three ways: published within `lookback_days`, listed
   under one of the allowed `categories`, and not already in
   [`state/seen.json`](state/seen.json).
3. Surviving papers are grouped by topic and written to `digests/YYYY-MM-DD.md`
   with title, arXiv ID, date, categories, authors and an abstract snippet.
4. Their IDs are appended to `seen.json`, so tomorrow's run won't repeat them.
5. The workflow commits both the digest and the updated state.

A paper matching two topics is reported once, under the first topic that claims
it. If nothing new turns up, no digest file is written and the workflow commits
nothing. An empty file every day would just be noise.

## Running it yourself

```bash
# print what today's digest would contain, write nothing
python fetch_papers.py --dry-run

# write digests/<today>.md and update the state
python fetch_papers.py

# widen the window (useful on a first run)
python fetch_papers.py --lookback 30
```

No dependencies, standard library only.

## Configuring it

[`config.json`](config.json):

| Key | Meaning |
| --- | --- |
| `lookback_days` | How far back a submission can be and still count as new |
| `max_results_per_query` | Papers requested per topic per run |
| `request_delay_seconds` | Pause between API calls, to stay polite |
| `categories` | arXiv categories to accept; empty list accepts all |
| `topics` | `label` plus the `phrases` searched for that topic |

Topics are phrase searches across title, abstract and authors, so
`"heart failure"` matches a paper in `q-bio.QM` that never appears in an audio
listing.

## Schedule

[`arxiv-digest.yml`](../.github/workflows/arxiv-digest.yml) runs at 07:00 UTC
daily and can be triggered by hand from the Actions tab, with an optional
lookback override. GitHub's shared runners often start cron jobs late; that is
expected and harmless here.

## What the arXiv API does under load

Three behaviours worth knowing, all found by hitting them:

- **An unbounded phrase search sorted by submission date times out.** The date
  window is therefore part of the query (`submittedDate:[… TO …]`) rather than a
  filter applied to the results. That also stops the result budget being spent
  on papers too old to report.
- **Bursts get `HTTP 429 Rate exceeded`, sometimes per IP rather than per
  client.** The fetcher honours `Retry-After` and backs off up to four times,
  and requests are paced `request_delay_seconds` apart per arXiv's request to
  API clients.
- **A topic can still fail after all that.** It is skipped and named in the
  digest footer instead of taking down the run; the next run picks up what it
  missed, because those papers never entered `seen.json`.

Re-running on a day that already has a digest appends to it rather than
replacing it, since a second run only finds what the first one missed.

Terms of use: <https://info.arxiv.org/help/api/tou.html>. Thank you to arXiv for
use of its open access interoperability.
