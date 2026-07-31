# Integrating into EnzymeX later

> This repository is an independent test environment. It is not the official
> EnzymeX production codebase and does not modify the live EnzymeX service.
> Nothing here has been deployed to, or pushed at, the EnzymeX repository.

## Portable core and required adapters

The search logic under `app/search/`, plus `app/fasta.py` and `app/schemas.py`,
is framework-independent. The reference builder is also reusable against a
compatible copied schema. EnzymeX still needs adapters for its settings,
scheduler, job/result persistence, artifact directories and result page. The
dependency edge runs one way:

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

`run_search` is synchronous, manages standalone job directories and returns a
Pydantic model. This call is the integration boundary, but the Pyramid
application must adapt it to EnzymeX's existing scheduled job, `job`,
`job_result`, `ref_data`, temporary-directory and result-page behavior.

## What must change once EnzymeX repository access is granted

1. **Confirm the real `enzymesdata` schema.** The alias table in
   `app/references/db.py` was written against the documented column list and
   verified against a synthetic fixture. The July 2026 handoff presentation
   shows `Source` and `UniprotID` but no marked primary key; `UniprotID` is now
   recognized as an accession alias, but the screenshot is not a substitute
   for inspecting the actual copy. Run `enzymex-refbuild inspect` first. If the
   real table has no primary key, the export needs a read-only copy-side view
   that supplies a deterministic key.

   Also inspect the real source vocabulary and counts. The exporter now fails
   closed without a source column and selects only normalized `swissprot` and
   `pdb` rows. The handoff documents say `enzymesdata` contains only subsets of
   the upstream databases, so a successful build must not be described as the
   complete Swiss-Prot or PDB corpus.

   The two-pass export requires InnoDB or a view whose source tables are
   transactional. The builder rejects a physical table with another reported
   engine; inspect the underlying tables separately when exporting through a
   view.

2. **Re-measure the build.** Every runtime figure in these docs comes from a
   1,574-reference synthetic fixture build. The clustering thresholds and QC
   gates are defaults chosen on that scale; on the real table, check
   `skipped_clusters.tsv` and
   `profile_member_coverage` in the manifest before trusting the profile layer,
   and expect `ENZYMEX_PROFILE_MIN_MEMBERS` to need raising if the number of
   accepted clusters makes the MAFFT loop too slow (see
   `docs/reference-build.md`).

3. **Decide where the reference build runs.** It needs read access to the
   copied database and write access to an artifact directory. It must not run
   inside a request. A cron job or a manual step after each database refresh
   is enough; the build id makes it obvious when the artifacts are stale.
   The standalone `all` command replaces export, BLAST and HMMER artifacts in
   stages, so keep its web process stopped during rebuilds. For EnzymeX, build
   a complete versioned generation and switch an active pointer only after all
   artifacts pass validation.

4. **Replace `app/web/` with EnzymeX views and templates.** The results page
   here is a reference implementation of *what to show*, not markup to lift.
   The three tables are deliberately separate (see the E-value comparability
   note in `docs/science.md`) and that separation should survive the port.

5. **Decide on execution mode.** Synchronous is fine at the scale measured
   here. EnzymeX already runs ECPICK, HIT-EC and CLEAN through a scheduler; if
   BLAST/HMMER results are to appear on the same result page, the natural
   thing is to run them as additional steps in that existing job rather than
   in the request. `run_search` is a plain function and will work either way.

6. **Reconcile with DIAMOND.** EnzymeX already uses DIAMOND internally to
   retrieve up to 20 similar proteins for interpretation, with a documented
   30% similarity cutoff and a same-predicted-EC random fallback when no hit is
   found. DIAMOND and blastp answer the same question at
   different sensitivity, so on the real page they should either be presented
   together with the difference labelled, or one of them should be dropped.
   Showing both without explanation invites the reader to treat two E-values
   from different tools as comparable. The proof-of-concept comparison in
   `results/comparison/comparison_report.md` quantifies the difference on a
   small set: DIAMOND's default mode missed the most distant positive query
   that `--very-sensitive`, BLAST and HMMER all found.

7. **Settle identifier policy.** References here are
   `EXR<stable copy/view key>`. If the real table lacks a primary key, the
   copy-side view must expose its deterministic unique key as `id`, which the
   schema detector accepts as the export key. `source_pk` stores that key.
   `UniprotID` values, including PDB-style identifiers in the handoff example,
   are currently embedded only in `description`; they are not a structured
   accession mapping. Preserve them in a dedicated metadata field during the
   EnzymeX integration if the result page needs them. Keep the safe internal
   ID for BLAST/HMMER deflines.

## Suggested order of work

1. Point `enzymex-refbuild inspect` at the real copy; verify its primary key,
   storage engine, source vocabulary, `UniprotID` mapping and representative
   PDB identifiers.
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
* `phmmer` is the slowest method and scales with reference count. It is the
  first thing to hit a timeout on a large copy.
* There is no authentication, rate limiting or CSRF protection here; EnzymeX
  presumably has its own, and this code assumes it.
* Job results are files on disk with a 24-hour retention, which is a test
  server's answer, not a production one.
