# Porsche Guru

A chat assistant for Porsche shoppers. It answers model questions from your own data table and
searches the web for live listings that match a model, specs and price range.

Under the hood it is Claude (`claude-opus-5-5`) with:

- two tools over your CSV model table (`describe_model_table`, `query_model_table`)
- Anthropic's server-side `web_search` and `web_fetch` tools for finding and reading listings
  (Porsche Finder, Bring a Trailer, Cars & Bids, PCARMARKET, CarGurus, Autotrader, and others)

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                         # then put your API key in .env
python -m porsche_guru                       # uses data/porsche_911.csv
python -m porsche_guru --data my_models.csv  # or point at another table
```

Web search must be enabled for your organization in the Claude Console.

## Web version and deployment

```bash
python -m porsche_guru.web    # open http://localhost:8080 (set PORT to change it)
```

The `Dockerfile` starts the same server on port 8080 and is what hosted deploys should use
(a `Procfile` with the same command is included for buildpack-style platforms). On the host,
set `ANTHROPIC_API_KEY` as an environment variable or secret, because `.env` is not committed.
`GET /healthz` returns `{"ok": true, ...}` for health checks.

## Model table

`data/porsche_911.csv` holds 288 Porsche 911 variants, from the 1964 2.0 to the 992, with 61
spec columns sourced from auto-data.net. The figures are metric, and Porsche Guru converts them
to US units in its answers. The table has no prices, so prices come from live listings.

Any CSV with a header row works: Claude reads the column names at runtime, and cells like
`$122,095`, `480 Hp @ 6500 rpm.` or `3.2` are compared as numbers.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `PORSCHE_GURU_DATA` | `data/porsche_models.csv` | Model table path |
| `PORSCHE_GURU_MODEL` | `claude-opus-5-5` | Claude model |
| `PORSCHE_GURU_EFFORT` | `high` | `low` / `medium` / `high` / `xhigh` / `max` (more effort means more thorough searching, at higher cost) |

## Tests

```bash
python -m unittest
```
