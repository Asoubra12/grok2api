"""Admin API: Geo-moderation test — single streaming endpoint with per-image progress."""

import asyncio
import io
import statistics
import time
from typing import Any, Dict, List, Optional

import aiohttp
import orjson
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.core.auth import verify_app_key
from app.core.logger import logger

router = APIRouter()


def _sse(event: dict) -> str:
    return f"data: {orjson.dumps(event).decode()}\n\n"


async def _get_proxy(session, api_key, sub_user_id, country):
    try:
        async with session.get(
            f"https://api.ipoasis.com/v1/proxy/dynamic/{sub_user_id}",
            params={"count": 1, "country": country, "protocol": "http", "sessionType": "sticky"},
            headers={"X-API-KEY": api_key},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = await resp.json()
            return str(data[0]) if isinstance(data, list) and data else None
    except Exception as e:
        logger.warning(f"Geo-test proxy failed {country}: {e}")
        return None


async def _set_proxy(session, grok_url, admin_key, proxy_url):
    try:
        await session.post(
            f"{grok_url}/v1/admin/config",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {admin_key}"},
            json={"proxy": {"base_proxy_url": proxy_url, "asset_proxy_url": proxy_url}},
            timeout=aiohttp.ClientTimeout(total=10),
        )
    except Exception:
        pass


async def _gen_one(session, grok_url, api_key, prompt):
    try:
        async with session.post(
            f"{grok_url}/v1/images/generations",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            json={"prompt": prompt, "model": "grok-imagine-1.0", "n": 1, "nsfw": True},
            timeout=aiohttp.ClientTimeout(total=90),
        ) as resp:
            return await resp.json()
    except Exception as e:
        return {"error": {"message": str(e)}}


async def _measure(session, url):
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                return 0, 0, 0
            data = await resp.read()
            w, h = 0, 0
            try:
                from PIL import Image
                with Image.open(io.BytesIO(data)) as img:
                    w, h = img.size
            except Exception:
                pass
            return len(data), w, h
    except Exception:
        return 0, 0, 0


def _score_country(images, images_per_country, min_normal_size, pass_threshold):
    ok_images = [img for img in images if img.get("status") == "ok" and img.get("size", 0) > 0]
    sizes = [img["size"] for img in ok_images]
    pass_count = sum(1 for img in images if img.get("status") == "ok" and img.get("size", 0) > min_normal_size)
    score = 0.0
    for img in images:
        s = img.get("size", 0)
        score += 1.0 if s > min_normal_size else (0.2 if s > 0 else 0.0)
    score = round(score / max(images_per_country, 1), 2)
    return {
        "ok_count": len(ok_images), "pass_count": pass_count, "score": score,
        "passed": pass_count >= pass_threshold,
        "error_count": sum(1 for img in images if img.get("status") == "error"),
        "avg_size": int(statistics.mean(sizes)) if sizes else 0,
        "min_size": min(sizes) if sizes else 0,
        "max_size": max(sizes) if sizes else 0,
    }


@router.post("/geo-test/run", dependencies=[Depends(verify_app_key)])
async def run_geo_test(data: dict = None):
    """Streams SSE per-image so the UI updates in real time."""
    from app.core.config import config, get_config

    if data:
        try:
            await config.update(data)
        except Exception:
            pass

    countries = get_config("geo_test.countries") or []
    prompt = str(get_config("geo_test.prompt") or "").strip()
    proxy_api_key = str(get_config("geo_test.proxy_api_key") or "").strip()
    proxy_sub_user_id = int(get_config("geo_test.proxy_sub_user_id") or 0)
    n_img = int(get_config("geo_test.images_per_country") or 4)
    min_size = int(get_config("geo_test.min_normal_size") or 50_000)
    pass_thr = int(get_config("geo_test.pass_threshold") or 2)
    grok_url = str(get_config("app.app_url") or "http://127.0.0.1:8000").rstrip("/")
    admin_key = str(get_config("app.app_key") or "")
    api_key = str(get_config("app.api_key") or get_config("app.public_key") or admin_key)

    errs = []
    if not prompt: errs.append("prompt")
    if not proxy_api_key: errs.append("proxy API key")
    if not proxy_sub_user_id: errs.append("proxy sub-user ID")
    if not countries: errs.append("countries")
    if errs:
        raise HTTPException(status_code=400, detail="Missing: " + ", ".join(errs))

    async def stream():
        all_results: List[Dict[str, Any]] = []
        t0 = time.time()
        total = len(countries)

        yield _sse({"type": "started", "total": total, "images_per_country": n_img})

        async with aiohttp.ClientSession() as session:
            for ci, cc in enumerate(countries):
                cc = str(cc).strip().lower()
                if not cc:
                    continue

                # --- Get proxy ---
                yield _sse({"type": "country_start", "country": cc, "index": ci, "total": total})

                proxy = await _get_proxy(session, proxy_api_key, proxy_sub_user_id, cc)
                if not proxy:
                    r = {"country": cc, "status": "NO_PROXY", "score": 0, "pass": False,
                         "total": n_img, "ok_count": 0, "pass_count": 0, "error_count": 0, "avg_size": 0}
                    all_results.append(r)
                    yield _sse({"type": "country_done", "index": ci, "total": total,
                                "processed": ci + 1, "result": r,
                                "ok": sum(1 for x in all_results if x.get("pass")),
                                "fail": sum(1 for x in all_results if not x.get("pass"))})
                    continue

                await _set_proxy(session, grok_url, admin_key, proxy)
                await asyncio.sleep(0.5)

                # --- Generate images one by one, yielding after each ---
                images: List[Dict[str, Any]] = []
                for img_i in range(n_img):
                    yield _sse({"type": "image_start", "country": cc, "image": img_i + 1, "of": n_img})

                    resp = await _gen_one(session, grok_url, api_key, prompt)

                    if "error" in resp:
                        msg = str(resp.get("error", {}).get("message", ""))[:120]
                        images.append({"status": "error", "error": msg, "size": 0})
                        yield _sse({"type": "image_done", "country": cc, "image": img_i + 1, "of": n_img,
                                    "status": "error", "error": msg})
                        continue

                    # Extract URL
                    img_url = None
                    dl = resp.get("data", [])
                    if isinstance(dl, list) and dl:
                        item = dl[0]
                        if isinstance(item, dict):
                            img_url = item.get("url") or item.get("b64_json")
                        elif isinstance(item, str) and item.startswith("http"):
                            img_url = item

                    if not img_url:
                        images.append({"status": "no_url", "size": 0})
                        yield _sse({"type": "image_done", "country": cc, "image": img_i + 1, "of": n_img,
                                    "status": "no_url"})
                        continue

                    fsize, w, h = await _measure(session, img_url)
                    images.append({"status": "ok", "size": fsize, "width": w, "height": h})
                    passed = "PASS" if fsize > min_size else "SMALL"
                    yield _sse({"type": "image_done", "country": cc, "image": img_i + 1, "of": n_img,
                                "status": "ok", "size": fsize, "width": w, "height": h, "verdict": passed})

                    await asyncio.sleep(0.3)

                # --- Country done ---
                await _set_proxy(session, grok_url, admin_key, "")
                stats = _score_country(images, n_img, min_size, pass_thr)
                r = {"country": cc, "status": "OK", "score": stats["score"],
                     "pass": stats["passed"], "total": n_img, **stats}
                all_results.append(r)

                yield _sse({"type": "country_done", "index": ci, "total": total,
                            "processed": ci + 1, "result": r,
                            "ok": sum(1 for x in all_results if x.get("pass")),
                            "fail": sum(1 for x in all_results if not x.get("pass"))})

            await _set_proxy(session, grok_url, admin_key, "")

        ranked = sorted(all_results, key=lambda x: (-x.get("score", 0), -x.get("avg_size", 0)))
        passed_count = sum(1 for r in all_results if r.get("pass"))
        yield _sse({"type": "done", "result": {
            "status": "success",
            "duration_sec": round(time.time() - t0, 1),
            "summary": {"total_countries": len(all_results), "passed": passed_count,
                        "failed": len(all_results) - passed_count,
                        "pass_threshold": pass_thr, "images_per_country": n_img},
            "ranked": ranked,
        }})

    headers = {
        "Cache-Control": "no-cache, no-store",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)
