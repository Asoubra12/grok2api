"""Admin API: Geo-moderation test endpoints."""

import asyncio

import orjson
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.auth import get_app_key, verify_app_key
from app.core.batch import create_task, expire_task, get_task
from app.core.logger import logger

router = APIRouter()


@router.post("/geo-test/run", dependencies=[Depends(verify_app_key)])
async def run_geo_test_async(data: dict = None):
    """Launch a geo-moderation test (async + SSE progress).

    Accepts optional overrides in request body that take precedence
    over the stored config, so the test always uses what's on screen.
    """
    from app.core.config import config, get_config

    # Merge body overrides into stored config first
    overrides = data or {}
    if overrides:
        try:
            await config.update(overrides)
        except Exception as e:
            logger.warning(f"Geo-test config save failed: {e}")

    countries = get_config("geo_test.countries") or []
    if not countries:
        raise HTTPException(status_code=400, detail="geo_test.countries is empty — configure countries first")

    prompt = str(get_config("geo_test.prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="geo_test.prompt is empty — enter a test prompt first")

    proxy_api_key = str(get_config("geo_test.proxy_api_key") or "").strip()
    if not proxy_api_key:
        raise HTTPException(status_code=400, detail="geo_test.proxy_api_key is empty — configure your proxy API key")

    proxy_sub_user_id = int(get_config("geo_test.proxy_sub_user_id") or 0)
    if not proxy_sub_user_id:
        raise HTTPException(status_code=400, detail="geo_test.proxy_sub_user_id is not set — configure your proxy sub-user ID")

    task = create_task(len(countries))

    async def _run():
        try:
            from app.services.grok.batch_services.geo_test import run_geo_test

            async def _on_country(result):
                is_pass = result.get("pass", False)
                task.record(
                    is_pass,
                    item=result.get("country", ""),
                    detail={
                        "country": result.get("country"),
                        "score": result.get("score"),
                        "pass": is_pass,
                        "pass_count": result.get("pass_count", 0),
                        "ok_count": result.get("ok_count", 0),
                        "total": result.get("total", 0),
                        "avg_size": result.get("avg_size", 0),
                        "status": result.get("status"),
                    },
                )

            result = await run_geo_test(
                on_country=_on_country,
                should_cancel=lambda: task.cancelled,
            )

            if task.cancelled:
                task.finish_cancelled()
                return

            task.finish(result)
        except Exception as e:
            logger.error(f"Geo-test failed: {e}")
            task.fail_task(str(e))
        finally:
            asyncio.create_task(expire_task(task.id, 600))

    asyncio.create_task(_run())

    return {
        "status": "success",
        "task_id": task.id,
        "total": len(countries),
    }


@router.get("/geo-test/{task_id}/stream")
async def geo_test_stream(task_id: str, request: Request):
    """SSE stream for geo-test progress."""
    app_key = get_app_key()
    if app_key:
        key = request.query_params.get("app_key")
        if key != app_key:
            raise HTTPException(status_code=401, detail="Invalid authentication token")

    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    async def event_stream():
        queue = task.attach()
        try:
            yield f"data: {orjson.dumps({'type': 'snapshot', **task.snapshot()}).decode()}\n\n"

            final = task.final_event()
            if final:
                yield f"data: {orjson.dumps(final).decode()}\n\n"
                return

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    final = task.final_event()
                    if final:
                        yield f"data: {orjson.dumps(final).decode()}\n\n"
                        return
                    continue

                yield f"data: {orjson.dumps(event).decode()}\n\n"
                if event.get("type") in ("done", "error", "cancelled"):
                    return
        finally:
            task.detach(queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/geo-test/{task_id}/cancel", dependencies=[Depends(verify_app_key)])
async def geo_test_cancel(task_id: str):
    """Cancel a running geo-test."""
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.cancel()
    return {"status": "success"}
