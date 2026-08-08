# The EnzymeX drop-in

This directory is the EnzymeX-side half of the project: a Pyramid view and two
templates that add BLAST/HMMER sequence search to ECPICK. Everything else in
this repository is the test server, which exists so this part can be developed
and checked without touching the live application.

Nothing here is copied into the `datax-lab/enzymex` repository by us. It sits
in this repository so the lab can review it as a diff, then apply it.

## Install

Two files and one directory move:

```
enzymex/views/search.py        ->  ecpick/views/search.py
enzymex/templates/search/      ->  ecpick/templates/search/
```

Then three lines in `ecpick/routes.py`, inside `includeme(config)`:

```python
    # Sequence search
    config.add_route('search', '/search')
    config.add_route('search_do', '/search.do')
    config.add_route('search_result', '/search/result')
```

`config.scan()` already picks the view up from the `@view_config` decorators;
there is nothing to register by hand.

Add a link to `search` wherever the navigation lives in `templates/layout.jinja2`.
We have not written that line, because it is a decision about the site's
navigation rather than about this feature.

## What it needs at runtime

- This project's `app` package importable, with its dependencies. It carries
  the search, the parsers and the result model. It is pure Python; the
  reference build is what needs BLAST+ and HMMER binaries, not the request path.
- `blastp` and `phmmer` on `PATH`, or their paths set in the environment
  (`ENZYMEX_BLASTP_BIN`, `ENZYMEX_PHMMER_BIN`).
- A reference build produced by `enzymex-refbuild`, with
  `ENZYMEX_REFERENCE_DIR` and `ENZYMEX_JOB_DIR` pointing at it. The build runs
  offline against the database; the view never opens a connection.

## Decisions worth knowing before review

**It runs synchronously and does not create a `Job`.** Against the 278,573
reference build, blastp answers in about 2.4 s and phmmer in about 7.8 s. The
existing queue exists because the deep models need a GPU and minutes; putting a
search behind it would make the search slower and add a table for nothing. The
process-wide concurrency bound in `app/search/service.py` is what keeps
simultaneous searches from exhausting memory, and an over-limit request gets a
"try again in a moment" rather than a queue slot.

**It does not require a login.** Nothing is written to the database, and the
cost per request is bounded, so it behaves like the enzyme browser rather than
like an EC submission. Change `required=False` to `required=True` in the view if
that is not the policy you want.

**The parameter panel is the full set.** E-value, hits per query, minimum query
coverage, scoring matrix, composition-based statistics and gap costs, each
pre-filled with the tool's own default and labelled as such. Every value is
bounded and whitelisted in `app/search/params.py` before it reaches a command
line, and the panel is collapsed by default so the neutral run is the one you
get without touching anything.

Two of those need context. `comp_based_stats` defaults to `2`, which is
blastp's own default and not an addition. The coverage filter exists because of
the Agave cross-check: ranking by identity selected short high-identity
fragments, and 30% of those annotations rested on an alignment covering under
half the query. It is off unless asked for.

**Autoescaping is load-bearing.** FASTA headers are user input and appear on the
result page. pyramid_jinja2 autoescapes `.jinja2` templates by default and
`tests/test_enzymex_view.py` asserts it, so a future change to that setting
fails the suite rather than the deployment.

## Open questions for the lab

1. **`ecpick/routes.py` is not in the `datax-lab/enzymex` repository.** That
   repository holds `views/`, `templates/`, `pull_data/` and `schedule/` only —
   no `routes.py`, no `models/`, no `ecpick/__init__.py`, no `.ini`. The three
   route lines above cannot be applied from it. Either those files come from
   somewhere else, or the change has to be made on the server.

2. **The `app` package name is generic.** Nothing in the ECPICK tree imports a
   top-level `app` today, so there is no collision, but it is a broad name to
   introduce into a shared deployment. Say the word and it gets a distinct name
   before anything is installed.

3. **Where results are stored.** Result JSON is written under
   `ENZYMEX_JOB_DIR` and deleted after `ENZYMEX_JOB_RETENTION_HOURS`. If EnzymeX
   would rather these lived in the database next to `Job`, that is a schema
   change and should be decided before the test server, not after.
