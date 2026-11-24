# research-automations

Automations that run my master's research workflow: literature tracking, experiment
bookkeeping, and the recurring chores around a speech-and-cardiac-signals
thesis. Each one is a tool I actually use, kept working rather than written once
and abandoned.

The research is on speech biomarkers for heart failure detection, so the topic
filters and metrics lean that way. The machinery is general.

## Tools

| Tool | What it does | Runs on |
| --- | --- | --- |
| [arxiv-digest](arxiv-digest/) | Pulls new arXiv papers on speech, cardiac and audio-ML topics, writes a dated Markdown digest, commits it | GitHub Actions, daily 07:00 UTC |

## How these are built

- **It has to run.** Every tool is executed against the real service before it
  lands, and its real output is committed. No untested code.
- **Standard library first.** A dependency is added only when the task cannot be
  done without it. `arxiv-digest` has none.
- **Idempotent.** Running twice does not duplicate work or double-post.
- **State is visible.** Anything a tool remembers between runs is a file in the
  repo, not a hidden cache. That makes it reviewable, and a bad run fixable with
  an edit.
- **Degrade, don't crash.** A scheduled job that loses one query should still
  deliver the rest and say what it missed.

## Layout

```
tool-name/
  README.md       what it does, how to run it, how to configure it
  *.py            the tool
  config.json     anything worth changing without editing code
  state/          what it remembers between runs
.github/workflows/
  tool-name.yml   the schedule, where one applies
```

## Running anything here locally

Python 3.9+ and no setup:

```bash
python arxiv-digest/fetch_papers.py --dry-run
```

Every tool supports `--dry-run`, which prints what it would do and writes
nothing.
