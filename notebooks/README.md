# notebooks/

Exploration and prototyping only — **not the source of truth**. Anything that must
run reproducibly graduates into the `h5n1/` library and an `ingest/` or `modal_jobs/`
entrypoint. Keep notebooks light; don't commit large outputs (cleared on save).

## Enclave notebooks — `<Platform>_YYYYMMDD<Letter>.ipynb`

Notebooks that run **inside a social platform's research enclave**, not on this repo's
stack, are committed here under a platform-prefixed, date-stamped, letter-suffixed name:

```
Facebook_20260822A.ipynb    first snapshot published that day
Facebook_20260822B.ipynb    second snapshot the same day
Facebook_20260823A.ipynb    next day resets to A
```

- **Prefix** is the source platform — `Facebook_`, and a different prefix (`X_`,
  `Reddit_`, …) when the platform changes.
- **Date** is the date the snapshot was published here, not a data date.
- **Letter** is the publish order *within that date*, `A` onward. It always starts at
  `A` even when only one file is published that day, so sort order is uniform, and it
  **resets to `A` on a new date**. The letter is a sequence marker, not a version
  number: `B` supersedes `A`, it does not branch from it.

### Where they run — Meta SRE

Facebook enclave notebooks are authored and executed inside the **Meta Secure Research
Environment (Meta SRE)**, a remote browser workspace fronting the Content Library API:

<https://cf77e6fb-8e36-46ed-9000-27d31063c1cb.workspaces-web.com/?deepLinks=https%3A%2F%2Fintern-content-library-api.fb-researchtool.com/>

Access requires an approved Meta Content Library account; the link alone is not
credentials. Everything inside stays inside — see the egress rule below.

Two rules for this series:

- **Authoring only.** They import the platform's in-enclave SDK
  (`metacontentlibraryapi` for Facebook), which does not exist locally. Nothing here
  runs, tests, or lints against a live API from this repo.
- **Outputs are always cleared.** Meta's Content Library permits **no data egress**, so
  a committed cell output would be a compliance violation, not just repo noise. Clear
  all outputs before saving — no exceptions.
