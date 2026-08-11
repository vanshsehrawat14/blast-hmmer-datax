# The EnzymeX drop-in

This directory is the EnzymeX-side half of the project: a Pyramid view and its
templates that add BLAST/HMMER sequence search to ECPICK. Everything else in
this repository is the test server, which exists so this part can be developed
and checked without touching the live application.

Nothing here is copied into the `datax-lab/enzymex` repository by us. It sits
in this repository so the lab can review it as a diff, then apply it.

It arrives in two places:

- **Under the model predictions on the EC result page**, as BLAST and HMMER
  buttons the reader can run to compare against what ECPICK, HIT-EC and CLEAN
  said. This is the part the lab asked for.
- **On its own page at `/search`**, for searching a sequence that has not been
  submitted as a job. Same code, same tables.

## Install

Two copies:

```
enzymex/views/search.py        ->  ecpick/views/search.py
enzymex/templates/search/      ->  ecpick/templates/search/
```

One line in `ecpick/routes.py`, inside `includeme(config)`:

```python
    config.include('ecpick.views.search')
```

That registers the four routes and the `request.sequence_search` the panel
reads its configuration from. It is one line rather than four so that a
half-applied install fails at startup instead of at the first click.
`config.scan()` still picks the views up from their `@view_config` decorators.

One include in `ecpick/templates/ec/result.jinja2`, inside the
`{% for results in job_result %}` loop, after the prediction tables:

```jinja
    {% with panel_id = outer_index,
            sequence = results[0].record_sequence,
            record_label = results[0].record_description %}
      {% include "ecpick:templates/search/_panel.jinja2" %}
    {% endwith %}
```

That edit is also here as `result-page.patch`, generated against the
`result.jinja2` in `datax-lab/enzymex` and applying at line 358 with six lines
added and nothing removed:

```
git apply --directory=. enzymex/result-page.patch     # from the ECPICK root
```

Apply the patch rather than the snippet if the deployed template still matches
that one. `tests/test_real_result_page.py` applies it to the real template,
renders the page through WSGI in ECPICK's own layout and fails if the anchor
has moved, so a template that has drifted shows up here rather than on the
server.

`ecpick/views/ec.py` does not change. The panel gets what it needs from
`request.sequence_search`, so the EC result view's return dict is left alone.

Add a link to `search` wherever the navigation lives in
`templates/layout.jinja2` if you want the standalone page reachable. We have
not written that line, because it is a decision about the site's navigation
rather than about this feature.

## What it needs at runtime

- This project's `app` package importable, with its dependencies. It carries
  the search, the parsers and the result model. It is pure Python; the
  reference build is what needs BLAST+ and HMMER binaries, not the request path.
- `blastp` and `phmmer` on `PATH`, or their paths set in the environment
  (`ENZYMEX_BLASTP_BIN`, `ENZYMEX_PHMMER_BIN`).
- A reference build produced by `enzymex-refbuild`, with
  `ENZYMEX_REFERENCE_DIR` and `ENZYMEX_JOB_DIR` pointing at it. The build runs
  offline against the database; the view never opens a connection.

If there is no build on the server, the panel renders nothing at all and the EC
result page is exactly what it is today.

## Decisions worth knowing before review

**Nothing runs on page load.** Each button is one request for one method. Both
methods together take about fifteen seconds against the 278,573-reference
build, and adding that to every EC result whether or not anyone scrolled down
would be a bad trade for a page people open to read a prediction.

**It runs synchronously and does not create a `Job`.** blastp answers in about
3 s and phmmer in about 12 s. The existing queue exists because the deep models
need a GPU and minutes; putting a search behind it would make the search slower
and add a table for nothing. The process-wide concurrency bound in
`app/search/service.py` is what keeps simultaneous searches from exhausting
memory, and an over-limit request gets a "try again in a moment" rather than a
queue slot.

**The panel endpoint requires a login; the standalone page does not.** The
panel lives on the EC result page, which redirects anonymous visitors to the
login, so the endpoint behind its buttons requires one too. The standalone
`/search` page writes nothing and costs a bounded amount, so it behaves like
the enzyme browser. Change `required=False` in `search_result` and `search` if
that is not the policy you want.

There is no ownership check on the sequence the panel posts. It arrives in the
request body, and `/search` already accepts arbitrary sequences from anyone, so
a check there would restrict nothing that is not already open. If `/search` is
put behind a login, this is worth revisiting together.

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

**The tables have one definition.** `_method.jinja2` renders the hits, and both
the standalone result page and the fragment the buttons fetch include it. The
buttons fetch HTML rather than JSON for that reason: the JavaScript inserts the
response and formats nothing, so the two places a hit can appear cannot drift
apart as columns change.

**Autoescaping is load-bearing.** FASTA headers and reference descriptions are
user input and appear in both. pyramid_jinja2 autoescapes `.jinja2` templates
by default and `tests/test_enzymex_view.py` asserts it, so a future change to
that setting fails the suite rather than the deployment.

## Open questions for the lab

1. **Which `routes.py` gets the line.** It is not in the `datax-lab/enzymex`
   repository, which holds `views/`, `templates/`, `pull_data/` and `schedule/`
   only. The local-server archive has one, but that archive is older than the
   deployed tree: its `ec/result.jinja2` predates HIT-EC and CLEAN, and its
   `routes.py` defines 23 routes where the current `views/` reference 42. So
   the line has to go into whatever `routes.py` the server actually runs, which
   needs either server access or someone applying it there.

2. **The `app` package name is generic.** Nothing in the ECPICK tree imports a
   top-level `app` today, so there is no collision, but it is a broad name to
   introduce into a shared deployment. Say the word and it gets a distinct name
   before anything is installed.

3. **Where results are stored.** Result JSON is written under
   `ENZYMEX_JOB_DIR` and deleted after `ENZYMEX_JOB_RETENTION_HOURS`. If EnzymeX
   would rather these lived in the database next to `Job`, that is a schema
   change and should be decided before the test server, not after.
