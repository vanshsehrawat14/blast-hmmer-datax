"""BLAST/HMMER sequence search, as an ECPICK view.

Drop-in for `ecpick/views/search.py`. It is the EnzymeX-side half of this
project: the portable core in `app/` does the work, and this file does nothing
but read a Pyramid request, call it, and hand a template a dict — the same
division `app/web/routes.py` keeps on the test server.

Two things differ deliberately from ECPICK's EC prediction flow:

  * It runs synchronously instead of queueing a `Job`. blastp answers in
    seconds against the full reference set, where the deep models need a GPU
    and minutes; putting a search behind that queue would make it slower and
    add a table for no gain.
  * It does not require a login. Nothing is written to the database and the
    cost per request is bounded, so it behaves like the enzyme browser rather
    than like a submission.

Nothing here opens a database connection. The reference set is built offline
by `enzymex-refbuild`, which is the only part of this project that reads
`enzymesdata`.
"""

from pyramid.httpexceptions import HTTPFound, HTTPMethodNotAllowed
from pyramid.renderers import render_to_response
from pyramid.view import view_config

from ecpick.views.base import get_login_user

from app import formatting
from app.config import get_settings
from app.fasta import FastaError, parse_submission
from app.jobs import load
from app.schemas import METHOD_LABELS
from app.search.params import (
    COMP_BASED_STATS_LABELS, MATRICES, ParamError, SearchParams,
)
from app.search.service import ALL_METHODS, ServerBusy, reference_status, run_search

FORM_TEMPLATE = 'ecpick:templates/search/search.jinja2'
RESULT_TEMPLATE = 'ecpick:templates/search/result.jinja2'

EXAMPLE_FASTA = """>sp|P00330|ADH1_YEAST Alcohol dehydrogenase 1
MSIPETQKGVIFYESHGKLEYKDIPVPKPKANELLINVKYSGVCHTDLHAWHGDWPLPVKL
PLVGGHEGAGVVVGMGENVKGWKIGDYAGIKWLNGSCMACEYCELGNESNCPHADLSGYTH
DGSFQQYATADAVQAAHIPQGTDLAQVAPILCAGITVYKALKSANLMAGHWVAISGAAGGL
GSLAVQYAKAMGYRVLGIDGGEGKEELFRSIGGEVFIDFTKEKDIVGAVLKATDGGAHGVI
NVSVSEAAIEASTRYVRANGTTVLVGMPAGAKCCSDVFNQVVKSISIVGSYVGNRADTREA
LDFFARGLVKSPIKVVGLSTLPEIYEKMEKGQIVGRYVVDTSK
"""


@view_config(route_name='search', renderer=FORM_TEMPLATE, request_method='GET')
def search(request):
    return _form_context(request)


@view_config(route_name='search_do', request_method='POST')
def search_do(request):
    """Run the search, then redirect so a refresh does not run it again."""
    if request.method != 'POST':
        raise HTTPMethodNotAllowed("'POST' method required")

    settings = get_settings()
    form = request.POST

    try:
        params = SearchParams.from_form(form, settings)
    except ParamError as exc:
        return _reject(request, str(exc))

    try:
        text = _submitted_text(form, settings)
    except ValueError as exc:
        return _reject(request, str(exc))

    try:
        records = parse_submission(
            text,
            max_sequences=settings.max_query_sequences,
            max_length=settings.max_query_length,
        )
    except FastaError as exc:
        return _reject(request, str(exc))

    methods = [m for m in form.getall('methods') if m in ALL_METHODS]

    try:
        result = run_search(settings, records, methods or None, params=params)
    except ServerBusy as exc:
        return _reject(request, str(exc))

    return HTTPFound(location=request.route_url('search_result',
                                                _query={'job': result.job_id}))


@view_config(route_name='search_result', renderer=RESULT_TEMPLATE,
             request_method='GET')
def search_result(request):
    settings = get_settings()
    result = load(settings, request.GET.get('job', ''))

    if result is None:
        # Not a 404 page: the job genuinely existed until retention expired,
        # and the useful next step is the form with an explanation on it.
        return _reject(
            request,
            'Those results are no longer stored. Results are kept for '
            f'{settings.job_retention_hours} hours, then deleted. '
            'Submit the sequences again to get a fresh search.',
        )

    return {
        'login_user': get_login_user(request, required=False),
        'result': result,
        'method_labels': METHOD_LABELS,
        # Number formatting lives in the portable core so this page and the CSV
        # cannot disagree about what a value means.
        'fmt': formatting,
        'thresholds': {
            'blastp': settings.blast_evalue,
            'phmmer': settings.phmmer_evalue,
            'hmmscan': settings.hmmscan_evalue,
        },
    }


# ---------------------------------------------------------------- internals

def _submitted_text(form, settings) -> str:
    """Pasted text, or the uploaded file if there is one.

    Read with an explicit cap rather than trusting the declared length, and
    decoded strictly so a binary upload is refused here instead of reaching
    the FASTA parser as replacement characters.
    """
    upload = form.get('fasta_file')
    if upload is not None and getattr(upload, 'filename', ''):
        raw = upload.file.read(settings.max_upload_bytes + 1)
        if len(raw) > settings.max_upload_bytes:
            raise ValueError(
                f'That file is larger than the '
                f'{settings.max_upload_bytes // 1000} kB limit.')
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            raise ValueError(
                'That file is not UTF-8 text. Upload a plain FASTA file.'
            ) from None

    text = form.get('sequences') or ''
    if len(text.encode('utf-8')) > settings.max_upload_bytes:
        raise ValueError(
            f'That submission is larger than the '
            f'{settings.max_upload_bytes // 1000} kB limit.')
    return text


def _reject(request, message: str):
    """Re-render the form with the reason and whatever was already typed."""
    context = _form_context(request, error=message)
    return render_to_response(FORM_TEMPLATE, context, request=request)


def _form_context(request, error: str | None = None) -> dict:
    settings = get_settings()
    form = request.POST if request.method == 'POST' else request.GET
    return {
        'login_user': get_login_user(request, required=False),
        'status': reference_status(settings),
        'settings': settings,
        'methods': ALL_METHODS,
        'method_labels': METHOD_LABELS,
        'matrices': MATRICES,
        'comp_based_stats': COMP_BASED_STATS_LABELS,
        'defaults': SearchParams.defaults(settings),
        'example': EXAMPLE_FASTA,
        'error': error,
        'submitted_text': (form.get('sequences') or '')[:20000],
        'selected_methods': form.getall('methods') if hasattr(form, 'getall') else [],
        'submitted': {k: v for k, v in form.items()
                      if k != 'fasta_file' and isinstance(v, str)},
    }
