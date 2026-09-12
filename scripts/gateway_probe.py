#!/usr/bin/env python3
"""Execute one short live-runner reservation with livepeer-python-gateway."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from livepeer_gateway import (
    MediaPublishConfig,
    StartJobRequest,
    parse_token,
    reserve_session,
    runner_selector,
    start_lv2v,
)


def _object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


async def _run(request: dict[str, Any]) -> dict[str, object]:
    encoded_token = request.get("sdk_token")
    capability = request.get("capability")
    timeout = request.get("timeout_seconds", 30)
    hold = request.get("hold_seconds", 2)
    action = request.get("action", "reserve")
    if not isinstance(encoded_token, str) or not encoded_token:
        raise ValueError("sdk_token must be a non-empty string")
    if not isinstance(capability, str) or not capability:
        raise ValueError("capability must be a non-empty string")
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("timeout_seconds must be positive")
    if not isinstance(hold, (int, float)) or hold < 0:
        raise ValueError("hold_seconds must be non-negative")
    if action not in {"reserve", "interrupt", "call", "lv2v"}:
        raise ValueError("action must be reserve, interrupt, call, or lv2v")

    token = parse_token(encoded_token)
    if action == "lv2v":
        return await _run_lv2v(request, encoded_token)
    if action == "call":
        payload = request.get("payload", {})
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        cursor = await runner_selector(
            body=payload,
            signer_url=token.get("signer"),
            signer_headers=token.get("signer_headers"),
            discovery_url=token.get("discovery"),
            discovery_headers=token.get("discovery_headers"),
            orchestrators=token.get("orchestrators"),
            app=capability,
            timeout=float(timeout),
        )
        result = await cursor.next()
        return {
            "status": "called",
            "session_id": result.session_id,
            "runner_url": result.runner_url,
            "content_type": result.content_type,
            "result": result.data,
            "content_bytes": len(result.content or b""),
        }
    session = await reserve_session(
        signer_url=token.get("signer"),
        signer_headers=token.get("signer_headers"),
        discovery_url=token.get("discovery"),
        discovery_headers=token.get("discovery_headers"),
        orchestrators=token.get("orchestrators"),
        app=capability,
        timeout=float(timeout),
    )
    try:
        if action == "interrupt":
            runner = session.runner
            print(
                json.dumps(
                    {
                        "status": "reserved",
                        "session_id": session.session_id,
                        "runner_url": session.runner_url,
                        "runner": {
                            "app": runner.app if runner else None,
                            "mode": runner.mode if runner else None,
                            "orchestrator_url": runner.orchestrator_url if runner else None,
                        },
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                flush=True,
            )
            await asyncio.Event().wait()
        await asyncio.sleep(float(hold))
        runner = session.runner
        price = runner.price_info.to_json() if runner and runner.price_info else None
        return {
            "status": "reserved",
            "session_id": session.session_id,
            "runner_url": session.runner_url,
            "app_url": session.app_url,
            "control_url": session.control_url,
            "runner": {
                "app": runner.app if runner else None,
                "mode": runner.mode if runner else None,
                "orchestrator_url": runner.orchestrator_url if runner else None,
                "price": price,
            },
        }
    finally:
        await session.aclose()


async def _run_lv2v(request: dict[str, Any], encoded_token: str) -> dict[str, object]:
    """Publish a bounded local media fixture through the gateway LV2V API."""

    import av

    payload = request.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("payload must be configured for LV2V")
    input_path = payload.get("input_path")
    model = payload.get("model")
    max_pixels = payload.get("max_pixels")
    if not isinstance(input_path, str) or not input_path:
        raise ValueError("input_path must be configured for LV2V")
    if not isinstance(model, str) or not model:
        raise ValueError("model must be configured for LV2V")
    if not isinstance(max_pixels, int) or isinstance(max_pixels, bool) or max_pixels <= 0:
        raise ValueError("max_pixels must be a positive integer")
    job = await asyncio.to_thread(
        start_lv2v,
        None,
        StartJobRequest(model_id=model, request_id="clearinghouse-qualification"),
        start_payments=False,
        token=encoded_token,
    )
    container = av.open(input_path)
    published_pixels = 0
    published_frames = 0
    try:
        job.start_payment_sender()
        publisher = job.start_media(MediaPublishConfig())
        for frame in container.decode(video=0):
            frame_pixels = frame.width * frame.height
            if published_pixels + frame_pixels > max_pixels:
                break
            await publisher.write_frame(frame)
            published_pixels += frame_pixels
            published_frames += 1
        if published_frames == 0:
            raise ValueError("LV2V input produced no frame within the pixel ceiling")
        await publisher.close()
        return {
            "status": "lv2v",
            "manifest_id": job.manifest_id,
            "published_frames": published_frames,
            "published_pixels": published_pixels,
            "has_publish_url": bool(job.publish_url),
            "has_subscribe_url": bool(job.subscribe_url),
        }
    finally:
        container.close()
        await job.close()


def main() -> int:
    """Read a secret-bearing request on stdin and emit a non-secret result."""

    try:
        request = _object(json.load(sys.stdin), "request")
        result = asyncio.run(_run(request))
    except Exception as error:  # noqa: BLE001 - CLI boundary reports SDK failures
        print(json.dumps({"status": "failed", "error": str(error)}))
        return 1
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
