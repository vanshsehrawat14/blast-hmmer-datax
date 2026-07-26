# Integrating into EnzymeX later

> This repository is an independent test environment. It is not the official
> EnzymeX production codebase and does not modify the live EnzymeX service.
> Nothing here has been deployed to, or pushed at, the EnzymeX repository.

## What is portable as-is

Everything under `app/references/` and `app/search/`, plus `app/fasta.py`,
`app/schemas.py`, `app/jobs.py` and `app/config.py`. None of it imports
FastAPI, Jinja2 or Starlette. The dependency edge runs one way:

```
app/web/  ──►  app/search/  ──►  app/references/  ──►  app/config.py
 (drop)          (keep)             (keep)               (merge)
```

The call a host application makes is:

```python
from app.fasta import parse_submission
from app.search.service import run_search

records = parse_submission(fasta_text, max_sequences=10, max_length=5000)
result  = run_search(settings, records, ["blastp", "phmmer", "hmmscan"])
# result is a JobResult; result.flat_rows() is the tabular form
```

`run_search` is synchronous, does its own job-directory management and
cleanup, and returns a Pydantic model. In a Pyramid view that is a single
call; the only thing to replace is the template.

## What must change once EnzymeX repository access is granted

1. **Confirm the real `enzymesdata` schema.** The alias table in
   `app/references/db.py` was written against the documented column list and
   verified against a synthetic fixture. Run `enzymex-refbuild inspect`
   against the actual copy first; if a column resolves to the wrong name or a
   needed one is missing, that table is the only thing to edit. If the real
   table has no primary key, the export needs a view that supplies one.

2. **Re-measure the build.** Every runtime figure in these docs comes from a
   2,380-reference build. The clustering thresholds and QC gates are defaults
   chosen on that scale; on the real table, check `skipped_clusters.tsv` and
   `profile_member_coverage` in the manifest before trusting the profile layer,
   and expect `ENZYMEX_PROFILE_MIN_MEMBERS` to need raising if the number of
   accepted clusters makes the MAFFT loop too slow (see
   `docs/reference-build.md`).

3. **Decide where the reference build runs.** It needs read access to the
   copied database and write access to an artifact directory. It must not run
   inside a request. A cron job or a manual step after each database refresh
   is enough; the build id makes it obvious when the artifacts are stale.

4. **Replace `app/web/` with EnzymeX views and templates.** The results page
   here is a reference implementation of *what to show*, not markup to lift.
   The three tables are deliberately separate — see the E-value comparability
   note in `docs/science.md` — and that separation should survive the port.

5. **Decide on execution mode.** Synchronous is fine at the scale measured
   here. EnzymeX already runs ECPICK, HIT-EC and CLEAN through a scheduler; if
   BLAST/HMMER results are to appear on the same result page, the natural
   thing is to run them as additional steps in that existing job rather than
   in the request. `run_search` is a plain function and will work either way.

6. **Reconcile with DIAMOND.** EnzymeX already uses DIAMOND internally to
   retrieve similar proteins. DIAMOND and blastp answer the same question at
   different sensitivity, so on the real page they should either be presented
   together with the difference labelled, or one of them should be dropped —
   showing both without explanation invites the reader to treat two E-values
   from different tools as comparable. The proof-of-concept comparison in
   `results/comparison/comparison_report.md` quantifies the difference on a
   small set: DIAMOND's default mode missed the most distant positive query
   that `--very-sensitive`, BLAST and HMMER all found.

7. **Settle identifier policy.** References here are `EXR<enzymesdata.id>`.
   If EnzymeX would rather surface UniProt accessions on the page, the
   mapping is already in `metadata.sqlite3` (`source_pk`, `description`) —
   change the display, not the internal identifier, because the internal one
   is what keeps deflines safe for the tools.

## Suggested order of work

1. Point `enzymex-refbuild inspect` at the real copy; fix the alias table if
   needed.
2. Run a full build with `ENZYMEX_EXPORT_LIMIT` set, then unset. Compare the
   manifests.
3. Wire `run_search` into a scratch EnzymeX view and check hits resolve to the
   right EC and source.
4. Port the results markup into the EnzymeX result page alongside the model
   predictions.
5. Decide DIAMOND's fate.
6. Move the build to whatever schedule the database refresh follows.

## Known limitations to carry across

* Profile HMMs cover only QC-passing clusters, never the whole reference set.
  The page must keep saying so.
* `phmmer` is the slowest method and scales with reference count — it is the
  first thing to hit a timeout on a large copy.
* There is no authentication, rate limiting or CSRF protection here; EnzymeX
  presumably has its own, and this code assumes it.
* Job results are files on disk with a 24-hour retention, which is a test
  server's answer, not a production one.
