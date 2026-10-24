from collections.abc import Mapping
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "web")

NOTICE = "adrobot_notice"


def page(request: Request, name: str, context: Mapping[str, object]) -> HTMLResponse:
    """Render a screen and clear a notice cookie so a refresh does not repeat it."""
    shown = dict(context)
    cookie = request.cookies.get(NOTICE)
    # The result of the action comes first. A screen notice never hides it.
    notices = [text for text in (cookie, shown.get("notice")) if isinstance(text, str) and text]
    shown["notices"] = notices
    response = templates.TemplateResponse(request, name, shown)
    if cookie:
        response.delete_cookie(NOTICE, path="/")
    return response


def failure(request: Request, message: str, *, back: str | None = None) -> RedirectResponse:
    """Send the operator back to the screen that submitted the action, with a notice.

    The message never carries the API key or a tracker body.
    """
    response = RedirectResponse(url=back or _screen(request), status_code=303)
    response.set_cookie(
        NOTICE,
        message,
        max_age=120,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


def _screen(request: Request) -> str:
    path = request.url.path
    if path == "/campaigns":
        return "/"
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "campaigns" and parts[1].isdigit():
        return f"/campaigns/{parts[1]}"
    return "/"
