"""Serves the React front end's index.html for every browser route (the app routes on the client). Sets the CSRF
cookie, so the app can make changes through the API from its first page."""
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import ensure_csrf_cookie

INDEX = settings.BASE_DIR / "frontend" / "dist" / "index.html"


@ensure_csrf_cookie
def index(request):
    if not INDEX.exists():
        return HttpResponse("<h1>The front end is not built</h1><p>Run <code>selfservice/frontend/build.sh</code> "
                            "(or restart the service: <code>lab.sh selfservice</code> builds it). The classic pages: "
                            "<a href='/classic/'>/classic/</a>.</p>", status=503)
    r = HttpResponse(INDEX.read_bytes(), content_type="text/html; charset=utf-8")
    r["Cache-Control"] = "no-cache"
    return r
