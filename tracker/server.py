"""
Dashboard server: the tracker UI at /, its API at /api/t, and the existing workflow
launcher (ui/server.py) at /legacy with its original /api/* routes.

While it runs it also polls: one tick at start-up (in the background, so pages load at
once) and then every `schedule.server_every_min` minutes — see tracker/scheduler.py.

    python -m tracker serve            # http://127.0.0.1:7788
    python -m tracker serve --no-poll  # pages only
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .api_orders import router as orders_router
from .config import WEB_DIR, load_settings
from .scheduler import poller

_POLL = {"on": True}


@asynccontextmanager
async def lifespan(_app):
    if _POLL["on"] and load_settings()["schedule"].get("server_poll", True):
        poller.start()
    yield
    poller.stop()


app = FastAPI(title="Stock Tracker", lifespan=lifespan)
app.include_router(router, prefix="/api/t")
app.include_router(orders_router, prefix="/api/t")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.middleware("http")
async def revalidate(request, call_next):
    """Local tool: always revalidate UI files and data so edits and syncs show up on reload."""
    response = await call_next(request)
    if request.url.path.startswith(("/static/", "/api/")):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


try:  # keep the workflow launcher available alongside the tracker
    from ui import server as legacy

    @app.get("/legacy", response_class=HTMLResponse, include_in_schema=False)
    def legacy_index():
        return HTMLResponse((legacy.UI_DIR / "index.html").read_text())

    for route in legacy.app.router.routes:
        if getattr(route, "path", "") not in ("/", "/design/{num}", "/openapi.json", "/docs", "/redoc",
                                              "/docs/oauth2-redirect"):
            app.router.routes.append(route)
except Exception as exc:  # pragma: no cover - legacy UI is optional
    print(f"legacy UI not mounted: {exc}")


def run(port: int | None = None, poll: bool = True) -> None:
    import uvicorn

    _POLL["on"] = poll
    port = port or load_settings()["dashboard"]["port"]
    print(f"Stock tracker on http://127.0.0.1:{port}{'' if poll else ' (background polling off)'}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
