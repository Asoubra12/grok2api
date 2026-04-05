from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from app.api.pages.asset_response import serve_embedded_asset

router = APIRouter()
STATIC_DIR = Path(__file__).resolve().parents[2] / "static"


@router.get("/admin", include_in_schema=False)
async def admin_root():
    return RedirectResponse(url="/admin/login")


@router.get("/admin/login", include_in_schema=False)
async def admin_login():
    return serve_embedded_asset(STATIC_DIR, "admin/pages/login.html", "text/html; charset=utf-8")


@router.get("/admin/config", include_in_schema=False)
async def admin_config():
    return serve_embedded_asset(STATIC_DIR, "admin/pages/config.html", "text/html; charset=utf-8")


@router.get("/admin/cache", include_in_schema=False)
async def admin_cache():
    return serve_embedded_asset(STATIC_DIR, "admin/pages/cache.html", "text/html; charset=utf-8")


@router.get("/admin/token", include_in_schema=False)
async def admin_token():
    return serve_embedded_asset(STATIC_DIR, "admin/pages/token.html", "text/html; charset=utf-8")


@router.get("/admin/geo-test", include_in_schema=False)
async def admin_geo_test():
    return serve_embedded_asset(STATIC_DIR, "admin/pages/geo_test.html", "text/html; charset=utf-8")
