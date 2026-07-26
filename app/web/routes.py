"""HTTP routes.

Deliberately thin: parse the request, call the service layer, render. No
subprocess call, no parsing and no SQL lives in this file, which is what makes
`app/search` liftable into EnzymeX without dragging FastAPI along.
"""

from __future__ import annotations

import csv
import io
import logging

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.fasta import FastaError, parse_submission
from app.jobs import load
from app.references import db as dbmod
from app.schemas import FLAT_SCHEMA, METHOD_LABELS
from app.search.service import ALL_METHODS, ServerBusy, reference_status, run_search
from app.web.app_paths import TEMPLATE_DIR

log = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
# Jinja2Templates autoescapes .html by default; make it explicit so a future
# refactor cannot quietly turn it off. Every user string on the results page —
# FASTA headers above all — depends on this.
templates.env.autoescape = True


def _fmt_evalue(value) -> str:
    """BLAST prints 0.0 when the E-value underflows; say so rather than
    rendering a zero that reads like a missing number."""
    if value is None:
        return "—"
    if value == 0:
        return "<1e-180"
    return f"{value:.1e}" if value < 0.01 else f"{value:.3g}"


def _fmt_fraction(value) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def _fmt_percent1(value) -> str:
    return "—" if value is None else f"{value:.1f}%"


templates.env.filters["evalue"] = _fmt_evalue
templates.env.filters["frac"] = _fmt_fraction
templates.env.filters["pct1"] = _fmt_percent1

EXAMPLE_FASTA = """>sp|P00330|ADH1_YEAST Alcohol dehydrogenase 1
MSIPETQKGVIFYESHGKLEYKDIPVPKPKANELLINVKYSGVCHTDLHAWHGDWPLPVKL
PLVGGHEGAGVVVGMGENVKGWKIGDYAGIKWLNGSCMACEYCELGNESNCPHADLSGYTH
DGSFQQYATADAVQAAHIPQGTDLAQVAPILCAGITVYKALKSANLMAGHWVAISGAAGGL
GSLAVQYAKAMGYRVLGIDGGEGKEELFRSIGGEVFIDFTKEKDIVGAVLKATDGGAHGVI
NVSVSEAAIEASTRYVRANGTTVLVGMPAGAKCCSDVFNQVVKSISIVGSYVGNRADTREA
LDFFARGLVKSPIKVVGLSTLPEIYEKMEKGQIVGRYVVDTSK
"""


def _settings():
    return get_settings()


@router.get("/")
def index(request: Request):
    settings = _settings()
    status = reference_status(settings)
    return templates.TemplateResponse(request, "index.html", {
        "settings": settings,
        "status": status,
        "methods": ALL_METHODS,
        "method_labels": METHOD_LABELS,
        "example": EXAMPLE_FASTA,
    })


@router.get("/about")
def about(request: Request):
    return templates.TemplateResponse(request, "about.html", {
        "settings": _settings(),
        "status": reference_status(_settings()),
    })


@router.post("/search")
async def search(
    request: Request,
    sequences: str = Form(default=""),
    methods: list[str] = Form(default=[]),
    fasta_file: UploadFile | None = File(default=None),
):
    settings = _settings()

    text = sequences or ""
    if fasta_file is not None and fasta_file.filename:
        # Read with a hard cap rather than trusting Content-Length alone.
        raw = await fasta_file.read(settings.max_upload_bytes + 1)
        if len(raw) > settings.max_upload_bytes:
            return _submission_error(
                request, settings,
                f"Uploaded file exceeds the {settings.max_upload_bytes // 1000} kB limit.",
                text, methods, status_code=413)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return _submission_error(
                request, settings,
                "The uploaded file is not UTF-8 text. Upload a plain FASTA file.",
                text, methods)

    if len(text.encode("utf-8")) > settings.max_upload_bytes:
        return _submission_error(
            request, settings,
            f"Submission exceeds the {settings.max_upload_bytes // 1000} kB limit.",
            "", methods, status_code=413)

    try:
        records = parse_submission(
            text,
            max_sequences=settings.max_query_sequences,
            max_length=settings.max_query_length,
        )
    except FastaError as exc:
        return _submission_error(request, settings, str(exc), text, methods)

    try:
        result = run_search(settings, records, methods or None)
    except ServerBusy as exc:
        return _submission_error(request, settings, str(exc), text, methods,
                                 status_code=503)

    return RedirectResponse(f"/jobs/{result.job_id}", status_code=303)


@router.get("/jobs/{job_id}")
def job_page(request: Request, job_id: str):
    settings = _settings()
    result = load(settings, job_id)
    if result is None:
        return templates.TemplateResponse(request, "error.html", {
            "title": "Job not found",
            "message": (
                "No results are stored under that identifier. Jobs on this test "
                f"server are deleted after {settings.job_retention_hours} hours."
            ),
        }, status_code=404)
    return templates.TemplateResponse(request, "results.html", {
        "settings": settings,
        "result": result,
        "method_labels": METHOD_LABELS,
    })


@router.get("/jobs/{job_id}/results.json")
def job_json(job_id: str):
    result = load(_settings(), job_id)
    if result is None:
        return JSONResponse({"error": "job not found"}, status_code=404)
    return JSONResponse(result.model_dump(mode="json"))


@router.get("/jobs/{job_id}/results.csv")
def job_csv(job_id: str):
    result = load(_settings(), job_id)
    if result is None:
        return JSONResponse({"error": "job not found"}, status_code=404)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(FLAT_SCHEMA), lineterminator="\n")
    writer.writeheader()
    writer.writerows(result.flat_rows())
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{job_id}_hits.csv"'},
    )


@router.get("/health")
def health():
    """Liveness plus everything a search depends on, without leaking secrets.

    The copied database is probed because the reference build needs it, even
    though a user request never touches it. A failing probe is reported as
    degraded rather than down: existing artifacts still serve searches.
    """
    settings = _settings()
    status = reference_status(settings)
    db_ok, db_detail = dbmod.ping(settings)

    artifacts_ok = status["artifacts"]["references_fasta"] and status["artifacts"]["metadata_db"]
    searchable = any(m["enabled"] and m["ok"] for m in status["methods"].values())

    if artifacts_ok and searchable:
        state = "ok" if db_ok else "degraded"
    else:
        state = "unavailable"

    body = {
        "status": state,
        "reference_build_id": status["reference_build_id"],
        "built_at": status["built_at"],
        "reference_sequences": status["reference_sequences"],
        "profiles": status["profiles"],
        "artifacts": status["artifacts"],
        "methods": {k: {"enabled": v["enabled"], "ok": v["ok"]}
                    for k, v in status["methods"].items()},
        "copied_database": {
            "target": settings.dsn_summary(),   # never includes the password
            "reachable": db_ok,
            "detail": db_detail if db_ok else db_detail[:200],
        },
        "tool_versions": status["tool_versions"],
    }
    return JSONResponse(body, status_code=200 if state != "unavailable" else 503)


def _submission_error(request: Request, settings, message: str, text: str,
                      methods: list[str], status_code: int = 400):
    """Re-render the form with the message and the user's input preserved."""
    return templates.TemplateResponse(request, "index.html", {
        "settings": settings,
        "status": reference_status(settings),
        "methods": ALL_METHODS,
        "method_labels": METHOD_LABELS,
        "example": EXAMPLE_FASTA,
        "error": message,
        "submitted_text": text[:20_000],
        "selected_methods": methods,
    }, status_code=status_code)
