"""Admin API: Geo-moderation test — single streaming endpoint."""

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


async def _get_proxy(session: aiohttp.ClientSession, api_key: str, sub_user_id: int, country: str) -> Optional[str]:
    try:
        async with session.get(
            f"https://api.ipoasis.com/v1/proxy/dynamic/{sub_user_id}",
            params={"count": 1, "country": country, "protocol": "http", "sessionType": "sticky"},
            headers={"X-API-KEY": api_key},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = await resp.json()
            if isinstance(data, list) and data:
                return str(data[0])
    except Exception as e:
        logger.warning(f"Geo-test proxy fetch failed for {country}: {e}")
    return None


async def _set_proxy(session: aiohttp.ClientSession, grok_url: str, admin_key: str, proxy_url: str) -> None:
    try:
        await session.post(
            f"{grok_url}/v1/admin/config",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {admin_key}"},
            json={"proxy": {"base_proxy_url": proxy_url, "asset_proxy_url": proxy_url}},
            timeout=aiohttp.ClientTimeout(total=10),
        )
    except Exception as e:
        logger.warning(f"Geo-test set_proxy failed: {e}")


async def _generate_image(session: aiohttp.ClientSession, grok_url: str, api_key: str, prompt: str) -> dict:
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


async def _download_and_measure(session: aiohttp.ClientSession, url: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {"size": 0, "width": 0, "height": 0}
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                return result
            data = await resp.read()
            result["size"] = len(data)
            try:
                from PIL import Image
                with Image.open(io.BytesIO(data)) as img:
                    result["width"], result["height"] = img.size
            except Exception:
                pass
    except Exception:
        pass
    return result


async def _test_country(
    session: aiohttp.ClientSession,
    country: str,
    *,
    proxy_api_key: str,
    proxy_sub_user_id: int,
    grok_url: str,
    admin_key: str,
    api_key: str,
    prompt: str,
    images_per_country: int,
    min_normal_size: int,
    pass_threshold: int,
) -> Dict[str, Any]:
    proxy_url = await _get_proxy(session, proxy_api_key, proxy_sub_user_id, country)
    if not proxy_url:
        return {"country": country, "status": "NO_PROXY", "score": 0, "pass": False,
                "total": images_per_country, "ok_count": 0, "pass_count": 0,
                "error_count": 0, "avg_size": 0, "images": []}

    await _set_proxy(session, grok_url, admin_key, proxy_url)
    await asyncio.sleep(0.5)

    images: List[Dict[str, Any]] = []
    for _ in range(images_per_country):
        resp = await _generate_image(session, grok_url, api_key, prompt)
        if "error" in resp:
            msg = str(resp.get("error", {}).get("message", "unknown"))[:120]
            images.append({"status": "error", "error": msg, "size": 0})
            continue

        img_url = None
        data_list = resp.get("data", [])
        if isinstance(data_list, list) and data_list:
            item = data_list[0]
            if isinstance(item, dict):
                img_url = item.get("url") or item.get("b64_json")
            elif isinstance(item, str) and item.startswith("http"):
                img_url = item

        if not img_url:
            images.append({"status": "no_url", "size": 0})
            continue

        measurement = await _download_and_measure(session, img_url)
        images.append({"status": "ok", "url": img_url, **measurement})
        await asyncio.sleep(0.3)

    # Clear proxy after this country
    await _set_proxy(session, grok_url, admin_key, "")

    ok_images = [img for img in images if img["status"] == "ok" and img["size"] > 0]
    sizes = [img["size"] for img in ok_images]
    pass_count = sum(1 for img in images if img["status"] == "ok" and img["size"] > min_normal_size)

    score = 0.0
    for img in images:
        s = img.get("size", 0)
        score += 1.0 if s > min_normal_size else (0.2 if s > 0 else 0.0)
    score = round(score / max(images_per_country, 1), 2)

    return {
        "country": country, "status": "OK", "score": score,
        "pass": pass_count >= pass_threshold,
        "total": images_per_country, "ok_count": len(ok_images),
        "pass_count": pass_count,
        "error_count": sum(1 for img in images if img["status"] == "error"),
        "avg_size": int(statistics.mean(sizes)) if sizes else 0,
        "min_size": min(sizes) if sizes else 0,
        "max_size": max(sizes) if sizes else 0,
        "images": images,
    }


@router.post("/geo-test/run", dependencies=[Depends(verify_app_key)])
async def run_geo_test(data: dict = None):
    """Run geo-moderation test. Streams SSE results inline — no background tasks."""
    from app.core.config import config, get_config

    overrides = data or {}
    if overrides:
        try:
            await config.update(overrides)
        except Exception as e:
            logger.warning(f"Geo-test config save failed: {e}")

    countries = get_config("geo_test.countries") or []
    prompt = str(get_config("geo_test.prompt") or "").strip()
    proxy_api_key = str(get_config("geo_test.proxy_api_key") or "").strip()
    proxy_sub_user_id = int(get_config("geo_test.proxy_sub_user_id") or 0)
    images_per_country = int(get_config("geo_test.images_per_country") or 4)
    min_normal_size = int(get_config("geo_test.min_normal_size") or 50_000)
    pass_threshold = int(get_config("geo_test.pass_threshold") or 2)

    grok_url = str(get_config("app.app_url") or "http://127.0.0.1:8000").rstrip("/")
    admin_key = str(get_config("app.app_key") or "")
    api_key = str(get_config("app.api_key") or get_config("app.public_key") or admin_key)

    errors = []
    if not prompt:
        errors.append("prompt is empty")
    if not proxy_api_key:
        errors.append("proxy API key is empty")
    if not proxy_sub_user_id:
        errors.append("proxy sub-user ID is not set")
    if not countries:
        errors.append("countries list is empty")
    if errors:
        raise HTTPException(status_code=400, detail="Missing config: " + "; ".join(errors))

    async def stream():
        all_results = []
        started = time.time()
        total = len(countries)

        yield _sse({"type": "started", "total": total})

        async with aiohttp.ClientSession() as session:
            for i, cc in enumerate(countries):
                cc = str(cc).strip().lower()
                if not cc:
                    continue

                try:
                    result = await _test_country(
                        session, cc,
                        proxy_api_key=proxy_api_key,
                        proxy_sub_user_id=proxy_sub_user_id,
                        grok_url=grok_url,
                        admin_key=admin_key,
                        api_key=api_key,
                        prompt=prompt,
                        images_per_country=images_per_country,
                        min_normal_size=min_normal_size,
                        pass_threshold=pass_threshold,
                    )
                except Exception as e:
                    logger.error(f"Geo-test country {cc} failed: {e}")
                    result = {"country": cc, "status": "ERROR", "score": 0, "pass": False,
                              "total": images_per_country, "ok_count": 0, "pass_count": 0,
                              "error_count": images_per_country, "avg_size": 0, "images": []}

                all_results.append(result)

                yield _sse({
                    "type": "progress",
                    "processed": i + 1,
                    "total": total,
                    "ok": sum(1 for r in all_results if r.get("pass")),
                    "fail": sum(1 for r in all_results if not r.get("pass")),
                    "detail": {
                        "country": result.get("country"),
                        "score": result.get("score"),
                        "pass": result.get("pass"),
                        "pass_count": result.get("pass_count", 0),
                        "ok_count": result.get("ok_count", 0),
                        "total": result.get("total", 0),
                        "avg_size": result.get("avg_size", 0),
                        "status": result.get("status"),
                    },
                })

            # Final proxy clear
            await _set_proxy(session, grok_url, admin_key, "")

        ranked = sorted(all_results, key=lambda x: (-x.get("score", 0), -x.get("avg_size", 0)))
        passed = sum(1 for r in all_results if r.get("pass"))

        yield _sse({
            "type": "done",
            "result": {
                "status": "success",
                "duration_sec": round(time.time() - started, 1),
                "summary": {
                    "total_countries": len(all_results),
                    "passed": passed,
                    "failed": len(all_results) - passed,
                    "pass_threshold": pass_threshold,
                    "images_per_country": images_per_country,
                },
                "ranked": ranked,
            },
        })

    return StreamingResponse(stream(), media_type="text/event-stream")
