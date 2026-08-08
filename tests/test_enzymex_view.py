"""Render the EnzymeX drop-in under real Pyramid.

The view in `enzymex/` is the half of this project that runs inside somebody
else's application, so it is the half that cannot be checked by reading it.
These tests build a miniature `ecpick` package by performing exactly the
install the drop-in's README describes, scan the `@view_config` decorators the
same way Pyramid does, and drive the result through WSGI.

The real `layout.jinja2` is not copied here — it belongs to EnzymeX. A stub
standing in for it declares the same blocks, which is what our templates
actually depend on.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.jobs import job_dir, save
from app.schemas import Hit, JobResult, MethodResult, MethodStatus, QueryResult

pyramid = pytest.importorskip("pyramid", reason="pip install '.[enzymex]'")
pytest.importorskip("pyramid_jinja2", reason="pip install '.[enzymex]'")

from pyramid.config import Configurator  # noqa: E402
from webob import Request  # noqa: E402

DROP_IN = Path(__file__).resolve().parent.parent / "enzymex"

# Only the blocks our templates fill. Deliberately not a copy of EnzymeX's
# layout: this asserts the contract between the templates, not their styling.
STUB_LAYOUT = """<!doctype html>
<html><head><title>{% block pageTitle %}{% endblock %}</title></head>
<body>
{% if login_user is none %}<span id="anon">anonymous</span>{% endif %}
{% block pageBody %}{% block pageContent %}{% endblock %}{% endblock %}
{% block script %}{% endblock %}
</body></html>
"""

STUB_BASE = '''"""Stands in for ecpick.views.base.

`logged_in` is flipped by the tests. The real one reads request.session and
raises RequiredLoginError when a login is required and absent, so this does the
same: the panel endpoint lives on a page that requires one.
"""

logged_in = False


class RequiredLoginError(Exception):
    pass


def get_login_user(request, required=False, admin=False):
    if not logged_in:
        if required:
            raise RequiredLoginError()
        return None
    return {'email': 'someone@example.edu'}
'''

# Stands in for ECPICK's EC result page: only the include the README asks them
# to add, in the loop position it asks for.
STUB_EC_RESULT = """{% extends "ecpick:templates/layout.jinja2" %}
{% block pageContent %}
{% for results in job_result %}
  <div class="card">{{ results[0].record_description }}</div>
  {% with panel_id = loop.index0,
          sequence = results[0].record_sequence,
          record_label = results[0].record_description %}
    {% include "ecpick:templates/search/_panel.jinja2" %}
  {% endwith %}
{% endfor %}
{% endblock %}
"""


@pytest.fixture(scope="module")
def ecpick_package(tmp_path_factory) -> Path:
    """Install the drop-in into a throwaway `ecpick` package, as documented."""
    root = tmp_path_factory.mktemp("deployment")
    pkg = root / "ecpick"
    (pkg / "views").mkdir(parents=True)
    (pkg / "templates").mkdir()

    (pkg / "templates" / "ec").mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "views" / "__init__.py").write_text("")
    (pkg / "views" / "base.py").write_text(STUB_BASE)
    (pkg / "templates" / "layout.jinja2").write_text(STUB_LAYOUT)
    (pkg / "templates" / "ec" / "result.jinja2").write_text(STUB_EC_RESULT)

    # The two copy steps the README asks for, and nothing else.
    shutil.copy(DROP_IN / "views" / "search.py", pkg / "views" / "search.py")
    shutil.copytree(DROP_IN / "templates" / "search", pkg / "templates" / "search")

    sys.path.insert(0, str(root))
    yield pkg
    sys.path.remove(str(root))
    for name in [m for m in sys.modules if m == "ecpick" or m.startswith("ecpick.")]:
        del sys.modules[name]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        reference_dir=tmp_path / "ref",
        job_dir=tmp_path / "jobs",
        db_password=SecretStr("unused-nothing-here-connects"),
    )


@pytest.fixture
def wsgi(ecpick_package, settings, monkeypatch):
    """A Pyramid app installed exactly the way the README documents."""
    import ecpick.views.base as base_module
    import ecpick.views.search as view_module

    monkeypatch.setattr(view_module, "get_settings", lambda: settings)
    monkeypatch.setattr(base_module, "logged_in", False)

    config = Configurator(settings={"pyramid.default_locale_name": "en"})
    config.include("pyramid_jinja2")
    # The one line the README asks EnzymeX to add to ecpick/routes.py.
    config.include("ecpick.views.search")

    # Stands in for ECPICK's own EC result route, so the panel is exercised
    # through an include from a foreign template rather than on its own.
    config.add_route("result", "/ec/result")
    config.add_view(_fake_ec_result, route_name="result",
                    renderer="ecpick:templates/ec/result.jinja2")

    config.scan("ecpick.views.search")
    return config.make_wsgi_app()


def _fake_ec_result(request):
    """The two fields the panel reads out of ECPICK's context."""
    class Row:
        record_description = "sp|P00330|ADH1_YEAST"
        record_sequence = "MKVLAAGIVGLGTVGSGVVRIL"

    return {"login_user": None, "job_result": [[Row()]]}


@pytest.fixture
def logged_in(monkeypatch):
    import ecpick.views.base as base_module
    monkeypatch.setattr(base_module, "logged_in", True)


@pytest.fixture
def canned_search(monkeypatch):
    """Replace the search itself; these tests are about the view and template.

    Returns the dict the fake records its arguments into, so the tests can
    check that the method and the parsed parameters actually reached it.
    """
    import ecpick.views.search as view_module

    seen: dict = {}

    def fake_run_search(settings, records, requested_methods=None, params=None, **kw):
        seen["records"] = records
        seen["methods"] = requested_methods
        seen["params"] = params
        return _result_object(requested_methods[0], params)

    monkeypatch.setattr(view_module, "run_search", fake_run_search)
    monkeypatch.setattr(view_module, "reference_status", lambda s: _canned_status())
    return seen


def _canned_status() -> dict:
    return {
        "reference_build_id": "161bdee61cbb",
        "reference_sequences": 278_573,
        "profiles": None,
        "artifacts": {"references_fasta": True, "metadata_db": True,
                      "blast_db": True, "profile_db": False},
        "methods": {
            "blastp": {"enabled": True, "ok": True, "detail": ""},
            "phmmer": {"enabled": True, "ok": True, "detail": ""},
            # Off, so the panel must not offer it.
            "hmmscan": {"enabled": True, "ok": False, "detail": "no profiles"},
        },
        "tool_versions": {},
    }


def _result_object(method: str, params) -> JobResult:
    return JobResult(
        job_id="a1b2c3d4e5f60718",
        reference_build_id="161bdee61cbb",
        reference_sequences=278_573,
        requested_methods=[method],
        search_parameters=params.as_dict() if params else {},
        total_runtime_seconds=2.4,
        queries=[QueryResult(
            query_id="Q1", query_description="sp|P00330|ADH1_YEAST",
            query_length=347,
            methods=[MethodResult(
                method=method, method_version=f"{method} 2.16.0+",
                status=MethodStatus.OK, runtime_seconds=2.4,
                hits=[Hit(rank=1, hit_id="EXRP00330", hit_description="ADH1",
                          hit_ec="1.1.1.1", hit_source="swissprot",
                          evalue=0.0, bitscore=712.0, percent_identity=99.4,
                          alignment_length=347, query_coverage=1.0,
                          subject_coverage=1.0)],
            )],
        )],
    )


def get(wsgi, path: str):
    return Request.blank(path).get_response(wsgi)


def post(wsgi, path: str, fields: dict):
    request = Request.blank(path, POST=fields)
    return request.get_response(wsgi)


# -- the form ---------------------------------------------------------------

def test_form_renders_for_an_anonymous_visitor(wsgi):
    response = get(wsgi, "/search")
    assert response.status_code == 200
    body = response.text
    assert 'name="sequences"' in body
    assert 'id="anon"' in body               # login not required
    assert "Search parameters" in body


def test_form_offers_every_parameter_with_its_default(wsgi):
    body = get(wsgi, "/search").text
    for field in ("evalue", "max_hits", "min_query_coverage", "matrix",
                  "comp_based_stats", "gapopen", "gapextend"):
        assert f'name="{field}"' in body, field
    assert "BLOSUM62 (default)" in body
    assert "conditional compositional adjustment (default)" in body


def test_form_posts_to_the_route_it_declares(wsgi):
    assert 'action="http://localhost/search.do"' in get(wsgi, "/search").text


# -- rejection paths --------------------------------------------------------

def test_a_bad_parameter_comes_back_on_the_form_not_as_a_traceback(wsgi):
    response = post(wsgi, "/search.do", {"sequences": ">q\nMKV\n", "evalue": "-1"})
    assert response.status_code == 200
    assert "Submission rejected" in response.text
    assert "greater than 0" in response.text


def test_a_rejected_submission_keeps_what_was_typed(wsgi):
    response = post(wsgi, "/search.do", {
        "sequences": ">kept\nMKVLAA\n", "evalue": "1e-9", "matrix": "NOPE",
    })
    body = response.text
    assert "Unsupported scoring matrix" in body
    assert "&gt;kept" in body               # escaped, and still in the textarea
    assert 'value="1e-9"' in body


def test_unparseable_fasta_is_rejected_by_the_form(wsgi):
    response = post(wsgi, "/search.do", {"sequences": "not fasta at all"})
    assert response.status_code == 200
    assert "Submission rejected" in response.text


def test_a_command_line_injection_attempt_is_refused(wsgi):
    response = post(wsgi, "/search.do", {
        "sequences": ">q\nMKVLAA\n", "matrix": "BLOSUM62 -out /tmp/pwned",
    })
    assert "Unsupported scoring matrix" in response.text


# -- the result page --------------------------------------------------------

def _stored_result(settings: Settings, description: str = "Alcohol dehydrogenase") -> JobResult:
    result = JobResult(
        job_id="a1b2c3d4e5f60718",
        reference_build_id="161bdee61cbb",
        reference_sequences=278_573,
        requested_methods=["blastp"],
        search_parameters={
            "evalue": 0.001, "max_hits": 25, "matrix": "BLOSUM62",
            "gapopen": None, "gapextend": None, "comp_based_stats": "2",
            "min_query_coverage": None,
        },
        total_runtime_seconds=2.4,
        queries=[QueryResult(
            query_id="Q1", query_description=description, query_length=347,
            methods=[MethodResult(
                method="blastp", method_version="blastp 2.16.0+",
                status=MethodStatus.OK, runtime_seconds=2.4,
                hits=[Hit(rank=1, hit_id="EXR000123", hit_description="ADH1",
                          hit_ec="1.1.1.1", hit_source="swissprot",
                          evalue=0.0, bitscore=712.0, percent_identity=99.4,
                          alignment_length=347, query_coverage=1.0,
                          subject_coverage=1.0)],
            )],
        )],
    )
    job_dir(settings, result.job_id, create=True)
    save(settings, result)
    return result


def test_result_page_renders_the_hit_table(wsgi, settings):
    _stored_result(settings)
    response = get(wsgi, "/search/result?job=a1b2c3d4e5f60718")
    assert response.status_code == 200
    body = response.text
    assert "EXR000123" in body
    assert "1.1.1.1" in body
    assert "161bdee61cbb" in body
    assert "99.4%" in body
    assert "&lt;1e-180" in body            # the underflowed E-value, escaped
    assert "Parameters used" in body
    assert "matrix defaults" in body       # gap costs left unset


def test_result_page_says_no_predictions_are_being_made(wsgi, settings):
    _stored_result(settings)
    body = get(wsgi, "/search/result?job=a1b2c3d4e5f60718").text
    assert "No EC prediction model runs here" in body


def test_an_expired_job_returns_the_form_with_an_explanation(wsgi):
    response = get(wsgi, "/search/result?job=doesnotexist")
    assert response.status_code == 200
    assert "no longer stored" in response.text
    assert 'name="sequences"' in response.text


def test_a_fasta_header_cannot_inject_markup(wsgi, settings):
    """Autoescaping is load-bearing here: descriptions are user input."""
    _stored_result(settings, description='<script>alert("xss")</script>')
    body = get(wsgi, "/search/result?job=a1b2c3d4e5f60718").text
    assert "<script>alert" not in body
    assert "&lt;script&gt;" in body


# -- the panel on ECPICK's EC result page -----------------------------------

def test_the_panel_renders_from_an_include_in_a_foreign_template(wsgi, canned_search):
    """The install is one include line; this is that line, in that position."""
    body = get(wsgi, "/ec/result").text
    assert "Run BLAST" in body
    assert 'data-sequence="MKVLAAGIVGLGTVGSGVVRIL"' in body
    assert 'data-endpoint="http://localhost/search/sequence.do"' in body


def test_the_panel_offers_only_methods_this_server_can_answer(wsgi, canned_search):
    body = get(wsgi, "/ec/result").text
    assert "Run BLAST" in body
    assert "phmmer" in body
    assert "hmmscan" not in body          # ok=False in the canned status


def test_the_panel_runs_nothing_on_page_load(wsgi, canned_search):
    body = get(wsgi, "/ec/result").text
    assert "Nothing has been run for this sequence yet" in body
    assert "EXRP00330" not in body


def test_the_panel_disappears_when_there_is_no_build(wsgi, monkeypatch):
    import ecpick.views.search as view_module

    empty = _canned_status()
    for method in empty["methods"].values():
        method["ok"] = False
    monkeypatch.setattr(view_module, "reference_status", lambda s: empty)

    body = get(wsgi, "/ec/result").text
    assert "js-seqsearch" not in body
    assert "sp|P00330|ADH1_YEAST" in body   # the page itself is unaffected


# -- the fragment endpoint --------------------------------------------------

FRAGMENT = "/search/sequence.do"
SEQUENCE = "MKVLAAGIVGLGTVGSGVVRIL"


def test_the_fragment_endpoint_refuses_an_anonymous_request(wsgi, canned_search):
    from ecpick.views.base import RequiredLoginError

    with pytest.raises(RequiredLoginError):
        post(wsgi, FRAGMENT, {"method": "blastp", "sequence": SEQUENCE})


def test_the_fragment_returns_the_hit_table_without_a_layout(wsgi, logged_in, canned_search):
    response = post(wsgi, FRAGMENT, {"method": "blastp", "sequence": SEQUENCE,
                                     "label": "sp|P00330|ADH1_YEAST"})
    assert response.status_code == 200
    body = response.text
    assert "EXRP00330" in body
    assert "1.1.1.1" in body
    assert "Parameters used" in body
    assert "<html" not in body           # a fragment, not a page
    assert canned_search["methods"] == ["blastp"]


def test_the_fragment_runs_only_the_method_that_was_asked_for(wsgi, logged_in, canned_search):
    post(wsgi, FRAGMENT, {"method": "phmmer", "sequence": SEQUENCE})
    assert canned_search["methods"] == ["phmmer"]


def test_parameters_from_the_panel_reach_the_search(wsgi, logged_in, canned_search):
    response = post(wsgi, FRAGMENT, {
        "method": "blastp", "sequence": SEQUENCE, "matrix": "PAM70",
        "max_hits": "5", "evalue": "1e-30", "min_query_coverage": "80",
    })
    params = canned_search["params"]
    assert params.matrix == "PAM70"
    assert params.max_hits == 5
    assert params.blast_evalue == 1e-30
    assert params.min_query_coverage == 0.8
    assert "PAM70" in response.text


def test_a_blank_parameter_leaves_the_default_alone(wsgi, logged_in, canned_search):
    post(wsgi, FRAGMENT, {"method": "blastp", "sequence": SEQUENCE, "matrix": ""})
    assert canned_search["params"].matrix == "BLOSUM62"


def test_the_fragment_refuses_an_unknown_method(wsgi, logged_in, canned_search):
    response = post(wsgi, FRAGMENT, {"method": "rm -rf /", "sequence": SEQUENCE})
    assert response.status_code == 400
    assert "Unknown search method" in response.text
    assert "methods" not in canned_search


def test_a_bad_parameter_comes_back_as_an_alert_not_a_traceback(wsgi, logged_in, canned_search):
    response = post(wsgi, FRAGMENT, {"method": "blastp", "sequence": SEQUENCE,
                                     "matrix": "BLOSUM62 -out /tmp/pwned"})
    assert response.status_code == 400
    assert "Unsupported scoring matrix" in response.text
    assert "methods" not in canned_search


def test_an_unusable_sequence_is_reported_not_searched(wsgi, logged_in, canned_search):
    response = post(wsgi, FRAGMENT, {"method": "blastp", "sequence": "123 456"})
    assert response.status_code == 400
    assert "Search not run" in response.text
    assert "methods" not in canned_search


def test_a_newline_in_the_label_cannot_smuggle_in_a_second_sequence(wsgi, logged_in, canned_search):
    """The label comes off the page, so it is still untrusted input here."""
    response = post(wsgi, FRAGMENT, {
        "method": "blastp", "sequence": SEQUENCE,
        "label": "harmless\n>smuggled\nMKVLAAGIVGLGTVGSGVVRIL",
    })
    assert response.status_code == 200
    assert len(canned_search["records"]) == 1
    assert "smuggled" not in canned_search["records"][0].sequence


def test_the_fragment_escapes_what_it_renders(wsgi, logged_in, monkeypatch):
    import ecpick.views.search as view_module

    monkeypatch.setattr(view_module, "reference_status", lambda s: _canned_status())
    monkeypatch.setattr(view_module, "run_search",
                        lambda *a, **k: _xss_result(a[2][0] if len(a) > 2 else "blastp"))

    body = post(wsgi, FRAGMENT, {"method": "blastp", "sequence": SEQUENCE}).text
    assert "<script>alert" not in body
    assert "&lt;script&gt;" in body


def _xss_result(method: str) -> JobResult:
    result = _result_object(method, None)
    result.queries[0].methods[0].hits[0].hit_description = '<script>alert("x")</script>'
    return result
