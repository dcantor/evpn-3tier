"""Live updates for the front end: GET /api/stream — server-sent events.

Once a second the stream looks for requests (that this user may see) changed since its last look and sends one
`request` event each: {id, status, seq, percent, stage}. The front end then fetches what it shows again (a list, a
request). Cheap — one indexed query a second per open tab — and needs no message broker. A comment every 15 s keeps
proxies from closing an idle connection; EventSource reconnects by itself after a drop."""
import asyncio
import json
import time

from asgiref.sync import sync_to_async
from django.http import HttpResponseForbidden, StreamingHttpResponse

from . import services


def _changes(user, since):
    qs = services.visible(user).filter(updated__gt=since).order_by("updated")[:50]
    return [{"id": r.pk, "status": r.status, "seq": (r.progress or {}).get("seq", 0), "percent": services.percent(r),
             "updated": r.updated.isoformat()} for r in qs], (qs[len(qs) - 1].updated if qs else since)


async def stream(request):
    user = await request.auser()
    if not user.is_authenticated: return HttpResponseForbidden("sign in first")
    from django.utils import timezone
    since = timezone.now()

    async def events():
        nonlocal since
        yield "retry: 3000\n\n"
        last_beat = time.monotonic()
        while True:
            changed, since = await sync_to_async(_changes)(user, since)
            for c in changed:
                yield f"event: request\ndata: {json.dumps(c)}\n\n"
            if time.monotonic() - last_beat > 15:
                yield ": keep-alive\n\n"; last_beat = time.monotonic()
            await asyncio.sleep(1)

    resp = StreamingHttpResponse(events(), content_type="text/event-stream")
    resp["Cache-Control"] = "no-cache"; resp["X-Accel-Buffering"] = "no"
    return resp
