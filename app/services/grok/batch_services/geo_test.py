"""
Geo-moderation test service.

Generates images through different country proxies and evaluates
whether the result is a real image or a censored/blocked placeholder.
"""

import asyncio
import io
import statistics
import time
from typing import Any, Callable, Dict, List, Optional

import aiohttp

from app.core.config import get_config
from app.core.logger import logger


async def _get_proxy(session: aiohttp.ClientSession, country: str) -> Optional[str]:
    """Fetch a mobile proxy URL for *country* from ipoasis."""
    api_key = str(get_config("geo_test.proxy_api_key") or "").strip()
    sub_user_id = int(get_config("geo_test.proxy_sub_user_id") or 0)
    if not api_key or not sub_user_id:
        return None
    try:
        url = f"https://api.ipoasis.com/v1/proxy/dynamic/{sub_user_id}"
        params = {
            "count": 1,
            "country": country,
            "protocol": "http",
            "sessionType": "sticky",
        }
        headers = {"X-API-KEY": api_key}
        async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json()
            if isinstance(data, list) and data:
                return str(data[0])
    except Exception as e:
        logger.warning(f"Geo-test proxy fetch failed for {country}: {e}")
    return None


async def _set_proxy(grok_url: str, admin_key: str, proxy_url: str) -> None:
    """Push proxy config to the grok2api instance."""
    try:
        async with aiohttp.ClientSession() as s:
            await s.post(
                f"{grok_url}/v1/admin/config",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {admin_key}",
                },
                json={"proxy": {"base_proxy_url": proxy_url, "asset_proxy_url": proxy_url}},
                timeout=aiohttp.ClientTimeout(total=10),
            )
    except Exception as e:
        logger.warning(f"Geo-test set_proxy failed: {e}")


async def _generate_image(
    session: aiohttp.ClientSession,
    grok_url: str,
    api_key: str,
    prompt: str,
) -> dict:
    """Generate one image via grok2api."""
    try:
        async with session.post(
            f"{grok_url}/v1/images/generations",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            json={
                "prompt": prompt,
                "model": "grok-imagine-1.0",
                "n": 1,
                "nsfw": True,
            },
            timeout=aiohttp.ClientTimeout(total=90),
        ) as resp:
            return await resp.json()
    except Exception as e:
        return {"error": {"message": str(e)}}


async def _download_size(session: aiohttp.ClientSession, url: str) -> int:
    """Download image and return byte size."""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 200:
                data = await resp.read()
                return len(data)
    except Exception:
        pass
    return 0


async def _download_and_measure(
    session: aiohttp.ClientSession, url: str
) -> Dict[str, Any]:
    """Download image, return size + dimensions."""
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


def _score_image(size: int, min_normal_size: int) -> float:
    """Score a single image: 1.0 = full-size, 0.2 = small, 0.0 = missing."""
    if size > min_normal_size:
        return 1.0
    if size > 0:
        return 0.2
    return 0.0


async def test_country(
    country: str,
    *,
    prompt: str,
    grok_url: str,
    admin_key: str,
    api_key: str,
    images_per_country: int,
    min_normal_size: int,
    pass_threshold: int,
) -> Dict[str, Any]:
    """Run the full test for one country.  Returns a result dict."""
    async with aiohttp.ClientSession() as session:
        # 1. Get proxy
        proxy_url = await _get_proxy(session, country)
        if not proxy_url:
            return {
                "country": country,
                "status": "NO_PROXY",
                "score": 0,
                "pass": False,
                "total": images_per_country,
                "ok_count": 0,
                "pass_count": 0,
                "error_count": 0,
                "avg_size": 0,
                "images": [],
            }

        # 2. Set proxy on instance
        await _set_proxy(grok_url, admin_key, proxy_url)
        await asyncio.sleep(0.5)

        images: List[Dict[str, Any]] = []
        for i in range(images_per_country):
            resp = await _generate_image(session, grok_url, api_key, prompt)

            if "error" in resp:
                msg = str(resp.get("error", {}).get("message", "unknown"))[:120]
                images.append({"status": "error", "error": msg, "size": 0, "width": 0, "height": 0})
                continue

            # Extract URL
            img_url = None
            data_list = resp.get("data", [])
            if isinstance(data_list, list) and data_list:
                item = data_list[0]
                if isinstance(item, dict):
                    img_url = item.get("url") or item.get("b64_json")
                elif isinstance(item, str) and item.startswith("http"):
                    img_url = item

            if not img_url:
                images.append({"status": "no_url", "size": 0, "width": 0, "height": 0})
                continue

            # Download and measure
            measurement = await _download_and_measure(session, img_url)
            images.append({
                "status": "ok",
                "url": img_url,
                **measurement,
            })
            await asyncio.sleep(0.3)

        # 3. Clear proxy
        await _set_proxy(grok_url, admin_key, "")

    # Scoring
    ok_images = [img for img in images if img["status"] == "ok" and img["size"] > 0]
    sizes = [img["size"] for img in ok_images]
    pass_count = sum(1 for img in images if img["status"] == "ok" and img["size"] > min_normal_size)

    score = 0.0
    for img in images:
        score += _score_image(img.get("size", 0), min_normal_size)
    score = round(score / max(images_per_country, 1), 2)

    return {
        "country": country,
        "status": "OK",
        "score": score,
        "pass": pass_count >= pass_threshold,
        "total": images_per_country,
        "ok_count": len(ok_images),
        "pass_count": pass_count,
        "error_count": sum(1 for img in images if img["status"] == "error"),
        "avg_size": int(statistics.mean(sizes)) if sizes else 0,
        "min_size": min(sizes) if sizes else 0,
        "max_size": max(sizes) if sizes else 0,
        "images": images,
    }


async def run_geo_test(
    *,
    on_country: Optional[Callable[[Dict[str, Any]], Any]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Run the full geo-moderation test across all configured countries.

    *on_country* is called with the result dict after each country completes.
    Returns the aggregate result.
    """
    prompt = str(get_config("geo_test.prompt") or "").strip()
    if not prompt:
        raise ValueError("geo_test.prompt is not configured")

    grok_url = str(get_config("app.app_url") or "http://127.0.0.1:8000").rstrip("/")
    admin_key = str(get_config("app.app_key") or "")
    api_key = str(get_config("app.api_key") or "")
    # Use public_key as fallback auth for image generation
    public_key = str(get_config("app.public_key") or "")
    effective_api_key = api_key or public_key or admin_key

    images_per_country = int(get_config("geo_test.images_per_country") or 4)
    min_normal_size = int(get_config("geo_test.min_normal_size") or 50_000)
    pass_threshold = int(get_config("geo_test.pass_threshold") or 2)
    countries = get_config("geo_test.countries") or []
    if not countries:
        raise ValueError("geo_test.countries is empty")

    all_results: List[Dict[str, Any]] = []
    started = time.time()

    for country in countries:
        if should_cancel and should_cancel():
            break

        country = str(country).strip().lower()
        if not country:
            continue

        logger.info(f"Geo-test: testing {country.upper()}...")
        result = await test_country(
            country,
            prompt=prompt,
            grok_url=grok_url,
            admin_key=admin_key,
            api_key=effective_api_key,
            images_per_country=images_per_country,
            min_normal_size=min_normal_size,
            pass_threshold=pass_threshold,
        )
        all_results.append(result)

        if on_country:
            try:
                ret = on_country(result)
                if asyncio.iscoroutine(ret):
                    await ret
            except Exception:
                pass

    # Clear proxy at the end
    try:
        await _set_proxy(grok_url, admin_key, "")
    except Exception:
        pass

    # Build summary
    total_countries = len(all_results)
    passed_countries = sum(1 for r in all_results if r.get("pass"))
    failed_countries = total_countries - passed_countries
    ranked = sorted(all_results, key=lambda x: (-x.get("score", 0), -x.get("avg_size", 0)))

    return {
        "status": "success",
        "duration_sec": round(time.time() - started, 1),
        "summary": {
            "total_countries": total_countries,
            "passed": passed_countries,
            "failed": failed_countries,
            "pass_threshold": pass_threshold,
            "images_per_country": images_per_country,
        },
        "ranked": ranked,
    }


__all__ = ["run_geo_test"]
