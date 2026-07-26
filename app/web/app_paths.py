"""Template and static locations, in their own module so routes.py and
app.py can both import them without a circular import."""

from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"
