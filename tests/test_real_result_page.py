"""Apply the drop-in to EnzymeX's real `ec/result.jinja2` and render it.

`tests/test_enzymex_view.py` proves the panel works against a stub result page.
This proves the same thing against the deployed one: the 617-line template from
`datax-lab/enzymex`, extending ECPICK's own `layout.jinja2`, with the include
applied by `enzymex/result-page.patch` rather than by hand.

It needs a checkout of that repository. Point `ENZYMEX_ECPICK_TREE` at it; the
tests skip when it is absent, because it is not ours to vendor.
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.config import Settings

pyramid = pytest.importorskip("pyramid", reason="pip install '.[enzymex]'")
pytest.importorskip("pyramid_jinja2", reason="pip install '.[enzymex]'")

from pyramid.config import Configurator  # noqa: E402
from webob import Request  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DROP_IN = REPO / "enzymex"
PATCH = DROP_IN / "result-page.patch"

_tree = os.environ.get("ENZYMEX_ECPICK_TREE")
TREE = Path(_tree) if _tree else Path.home() / "work" / "enzymex"

pytestmark = pytest.mark.skipif(
    not (TREE / "templates" / "ec" / "result.jinja2").is_file(),
    reason=f"no ECPICK templates at {TREE}; set ENZYMEX_ECPICK_TREE",
)

# What the panel is anchored to: the close of the per-sequence card inside the
# `job_result` loop. Kept as the patch's context lines so a template change that
# moves it fails here rather than on the server.
ANCHOR = "        </div>\n      </div>\n      <br>\n      <br>\n    {% endfor %}"

BASE = '''
logged_in = True


class RequiredLoginError(Exception):
    pass


def get_login_user(request, required=False, admin=False):
    if not logged_in:
        if required:
            raise RequiredLoginError()
        return None
    return {'email': 'someone@example.edu'}
'''

SEQUENCE = (
    "MSIPETQKGVIFYESHGKLEYKDIPVPKPKANELLINVKYSGVCHTDLHAWHGDWPLPVKLPLVGGHEGAGVVVGMGENVK"
    "GWKIGDYAGIKWLNGSCMACEYCELGNESNCPHADLSGYTHDGSFQQYATADAVQAAHIPQGTDLAQVAPILCAGITVYKA"
)
LABEL = "sp|P00330|ADH1_YEAST Alcohol dehydrogenase 1"


class Row:
    """One `JobResult`, as `views/ec.py` hands them to the template."""

    def __init__(self, model: str, confidence: float, idx: int) -> None:
        self.model = model
        self.predict_class = "1.1.1.1"
        self.predict_rec_name = "alcohol dehydrogenase"
        self.predict_confidence = confidence
        self.error_tag = None
        self.record_id = "Q1"
        self.record_sequence = SEQUENCE
        self.record_description = LABEL
        self.result_idx = idx
        self.subset = 0
        self.graph = None


class ModelName:
    tag_name = "2026-08-01"
    comment = "test weights"


class Job:
    job_idx = 1
    target_name = "demo"
    status = "Status.Finished"
    created_date_time = datetime(2026, 8, 11, 9, 0)
    started_date_time = datetime(2026, 8, 11, 9, 0)
    finished_date_time = datetime(2026, 8, 11, 9, 1)

    def __str__(self) -> str:
        return "Status.Finished"


@pytest.fixture(scope="module")
def deployment(tmp_path_factory) -> Path:
    """The real templates plus the drop-in, installed as documented."""
    root = tmp_path_factory.mktemp("ecpick-real")
    pkg = root / "ecpick"
    (pkg / "views").mkdir(parents=True)
    (pkg / "static").mkdir()

    shutil.copytree(TREE / "templates", pkg / "templates")
    (pkg / "__init__.py").write_text("")
    (pkg / "views" / "__init__.py").write_text("")
    (pkg / "views" / "base.py").write_text(BASE)

    shutil.copy(DROP_IN / "views" / "search.py", pkg / "views" / "search.py")
    shutil.copytree(DROP_IN / "templates" / "search", pkg / "templates" / "search")

    result = pkg / "templates" / "ec" / "result.jinja2"
    text = result.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert ANCHOR in text, "the anchor the patch applies at is gone from result.jinja2"
    result.write_text(text.replace(ANCHOR, _patched_anchor(), 1), encoding="utf-8")

    sys.path.insert(0, str(root))
    yield pkg
    sys.path.remove(str(root))
    for name in [m for m in sys.modules if m == "ecpick" or m.startswith("ecpick.")]:
        del sys.modules[name]


def _patched_anchor() -> str:
    """The anchor with the patch's added lines spliced in."""
    added = [
        line[1:]
        for line in PATCH.read_text(encoding="utf-8").splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    head, tail = ANCHOR.split("      <br>\n", 1)
    return head + "\n".join(added) + "\n      <br>\n" + tail


@pytest.fixture(scope="module")
def wsgi(deployment):
    import ecpick.views.search as view_module

    settings = Settings(db_password=SecretStr("unused-nothing-here-connects"))
    view_module.get_settings = lambda: settings

    def result_view(request):
        return {
            "login_user": {"email": "someone@example.edu"},
            "job": Job(),
            "job_result": [[Row("ECPICK", 87.4, 1),
                            Row("HIT-EC", 62.0, 2),
                            Row("CLEAN", -1, 3)]],
            "model_name": ModelName(),
            "ref_data": [],
            "len": len,
            "data_uris": {},
            "subset": [0],
            "interpretations": False,
            "motif_coords": [],
        }

    config = Configurator(settings={"pyramid.default_locale_name": "en"})
    config.include("pyramid_jinja2")
    config.add_static_view("static", "ecpick:static")
    config.include("ecpick.views.search")
    config.add_route("result", "/ec/result")
    config.add_view(result_view, route_name="result",
                    renderer="ecpick:templates/ec/result.jinja2")
    config.scan("ecpick.views.search")
    return config.make_wsgi_app()


@pytest.fixture(scope="module")
def page(wsgi) -> str:
    response = Request.blank("/ec/result").get_response(wsgi)
    assert response.status_code == 200
    return response.text


def test_the_patch_applies_to_the_deployed_template(deployment):
    rendered = (deployment / "templates" / "ec" / "result.jinja2").read_text()
    assert "ecpick:templates/search/_panel.jinja2" in rendered


def test_the_panel_renders_inside_the_real_page(page):
    assert "blastp" in page
    assert "phmmer" in page


def test_the_panel_sits_under_the_model_predictions(page):
    for model in ("ECPICK", "HIT-EC", "CLEAN"):
        assert page.index(model) < page.index("blastp"), model


def test_no_button_for_a_method_without_a_build(page):
    """No profile layer at this scale, so no hmmscan button rather than a dead one."""
    assert "hmmscan" not in page


def test_nothing_searches_on_page_load(page):
    assert "EXR" not in page


def test_one_panel_per_submitted_sequence(wsgi):
    assert Request.blank("/ec/result").get_response(wsgi).text.count(
        'data-method="blastp"'
    ) == 1
