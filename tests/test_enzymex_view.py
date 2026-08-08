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

STUB_BASE = '''"""Stands in for ecpick.views.base."""


def get_login_user(request, required=False, admin=False):
    return None
'''


@pytest.fixture(scope="module")
def ecpick_package(tmp_path_factory) -> Path:
    """Install the drop-in into a throwaway `ecpick` package, as documented."""
    root = tmp_path_factory.mktemp("deployment")
    pkg = root / "ecpick"
    (pkg / "views").mkdir(parents=True)
    (pkg / "templates").mkdir()

    (pkg / "__init__.py").write_text("")
    (pkg / "views" / "__init__.py").write_text("")
    (pkg / "views" / "base.py").write_text(STUB_BASE)
    (pkg / "templates" / "layout.jinja2").write_text(STUB_LAYOUT)

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
    """A Pyramid app carrying only the three routes the drop-in adds."""
    import ecpick.views.search as view_module

    monkeypatch.setattr(view_module, "get_settings", lambda: settings)

    config = Configurator(settings={"pyramid.default_locale_name": "en"})
    config.include("pyramid_jinja2")
    # The three lines the README asks EnzymeX to add to ecpick/routes.py.
    config.add_route("search", "/search")
    config.add_route("search_do", "/search.do")
    config.add_route("search_result", "/search/result")
    config.scan("ecpick.views.search")
    return config.make_wsgi_app()


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
