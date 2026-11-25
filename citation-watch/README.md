# citation-watch

[![Citation watch](https://github.com/NehaCh11/research-automations/actions/workflows/citation-watch.yml/badge.svg)](https://github.com/NehaCh11/research-automations/actions/workflows/citation-watch.yml)

Watches the published literature through the [OpenAlex](https://openalex.org)
API and commits a dated digest to [`digests/`](digests/).

## Why this exists alongside arxiv-digest

[arxiv-digest](../arxiv-digest/) covers preprints, and keyword search only.
This covers the two things it structurally cannot:

- **The citation graph.** "What has cited the three papers this thesis rests
  on" is not a keyword query, and no keyword list finds it reliably. A paper
  that builds directly on GeMAPS need not mention any of my search terms.
- **The published literature.** arXiv carries almost no clinical cardiology,
  and proceedings like Interspeech and Computing in Cardiology never appear
  there at all. The first run surfaced work in Frontiers in Digital Health,
  JACC Heart Failure and Interspeech, none of which arXiv would have shown.

Preprints are excluded by default, so the two tools never report the same work.

## How it works

1. Each seed in [`config.json`](config.json) becomes a `cites:<id>` query, and
   each topic becomes a search query, both bounded to `lookback_days`.
2. Anything already in [`state/seen.json`](state/seen.json) is dropped, as is a
   paper whose title was already claimed in this run.
3. What remains is grouped by query and written to `digests/YYYY-MM-DD.md` with
   venue, type, date, citation count, authors and an abstract snippet.
4. The workflow commits the digest and the updated state.

Nothing new means no file is written and nothing is committed. Re-running on a
day that already has a digest appends to it rather than replacing it.

## Running it yourself

```bash
python watch.py --dry-run          # print, write nothing
python watch.py                    # write digests/<today>.md
python watch.py --lookback 90      # widen the window
python -m unittest test_watch      # 27 tests, no network
```

No dependencies, standard library only.

## Configuring it

| Key | Meaning |
| --- | --- |
| `lookback_days` | How far back a publication date can be and still count |
| `max_results_per_query` | Papers fetched per query per run |
| `request_delay_seconds` | Pause between calls. OpenAlex throttles hard without a mailto |
| `exclude_preprints` | Keeps this tool off arxiv-digest's territory |
| `seeds` | Papers whose citations to follow, by OpenAlex id |
| `topics` | `label`, `phrase`, and the `field` to search |

### Why `field` is per topic

Search field decides precision, and the right one differs by phrase. Measured
over a 30 day window:

| Query | Matches | What came back |
| --- | --- | --- |
| `title_and_abstract: data leakage clinical machine learning evaluation` | 74 | retinal OCT, solar irradiance forecasting |
| `title_and_abstract: "data leakage"` | 426 | mostly cybersecurity, where the phrase means something else |
| `title: "data leakage"` | 57 | leakage in stroke prediction, patient-level leakage in respiratory sound |

So `"data leakage"` is searched in titles only, while `"voice biomarker"`, which
is unambiguous, is searched across title and abstract and returns 6 highly
relevant works in the same window.

A quoted phrase is an exact match. An unquoted one matches its terms loosely.

### Finding a seed's OpenAlex id

```bash
curl -s 'https://api.openalex.org/works?search=YOUR+PAPER+TITLE&per-page=2' \
  | python -c "import json,sys; [print(w['id'], w['display_name'][:70]) for w in json.load(sys.stdin)['results']]"
```

## The polite pool

OpenAlex serves anonymous requests from a shared pool that returns `429`
readily, which is why calls are paced 12 seconds apart and backed off on
failure. Supplying an email joins the faster polite pool.

That is opt-in and never committed, because this repo is public. Set an
`OPENALEX_MAILTO` repository secret and the workflow passes it through; without
it the tool runs on the common pool and says so in its log.

## Honest limits

- **A capped query is announced, not hidden.** If OpenAlex holds more matches
  than `max_results_per_query`, the digest says how many, because silently
  dropping the rest would look exactly like there being nothing else.
- **Seeds vary wildly in volume.** GeMAPS attracts roughly 28 citing works a
  month because half of speech computing uses it. The two heart failure seeds
  attract about 2. That is the citation graph being honest, not a bug.
- **`"data leakage"` still pulls in other fields.** Financial fraud and
  structural engineering use the same phrase. Roughly half that section is
  off-topic, and the trade is that it catches work like patient-level leakage
  in respiratory sound classification, which no cardiac keyword would find.

Data from OpenAlex, CC0.
