# ingest/

Thin CLI entrypoints, one per source, that call `h5n1.sources.*` and `h5n1.clean.*`.

Ownership:
- `usda.py` — Nick (reference implementation)
- `noaa.py` — Waree
- `usgs.py` — Max

Each follows the same shape: pull raw -> GCS + manifest -> stage -> clean -> load fact table -> data-quality check.
