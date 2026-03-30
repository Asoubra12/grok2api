from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from app.api.pages.asset_response import serve_embedded_asset
from app.core.auth import is_public_enabled

router = APIRouter()
STATIC_DIR = Path(__file__).resolve().parents[2] / "static"


@router.get("/", include_in_schema=False)
async def root():
    if is_public_enabled():
        return RedirectResponse(url="/login")
    return RedirectResponse(url="/admin/login")


@router.get("/login", include_in_schema=False)
async def public_login():
    if not is_public_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    return serve_embedded_asset(STATIC_DIR, "func/pages/login.html", "text/html; charset=utf-8")


@router.get("/imagine", include_in_schema=False)
async def public_imagine():
    if not is_public_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    return serve_embedded_asset(STATIC_DIR, "func/pages/imagine.html", "text/html; charset=utf-8")


@router.get("/voice", include_in_schema=False)
async def public_voice():
    if not is_public_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    return serve_embedded_asset(STATIC_DIR, "func/pages/voice.html", "text/html; charset=utf-8")


@router.get("/video", include_in_schema=False)
async def public_video():
    if not is_public_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    return serve_embedded_asset(STATIC_DIR, "func/pages/video.html", "text/html; charset=utf-8")


@router.get("/chat", include_in_schema=False)
async def public_chat():
    if not is_public_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    return serve_embedded_asset(STATIC_DIR, "func/pages/chat.html", "text/html; charset=utf-8")


@router.get("/nsfw", include_in_schema=False)
async def public_nsfw():
    if not is_public_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    return serve_embedded_asset(STATIC_DIR, "func/pages/nsfw.html", "text/html; charset=utf-8")


@router.get("/imagine-workbench", include_in_schema=False)
async def public_imagine_workbench():
    if not is_public_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    return serve_embedded_asset(STATIC_DIR, "func/pages/imagine_workbench.html", "text/html; charset=utf-8")


@router.get("/manifest.webmanifest", include_in_schema=False)
async def public_manifest():
    if not is_public_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    return serve_embedded_asset(STATIC_DIR, "func/manifest.webmanifest", "application/manifest+json")


@router.get("/sw.js", include_in_schema=False)
async def public_service_worker():
    if not is_public_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    return serve_embedded_asset(STATIC_DIR, "func/sw.js", "application/javascript")
