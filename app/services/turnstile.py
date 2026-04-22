"""Cloudflare Turnstile server-side verification."""

import httpx
from fastapi import HTTPException, status

from app.config import get_settings

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


async def verify_turnstile(token: str, ip: str | None = None) -> None:
    """Verify a Cloudflare Turnstile token.

    Raises HTTP 400 if verification fails.
    Silently passes when no secret key is configured (local dev without keys).
    """
    settings = get_settings()
    secret = settings.cf_turnstile_secret_key

    # Skip when no secret key is configured (local dev) or no token was sent
    # (admin panel, legacy clients, browsers with Turnstile blocked).
    if not secret or not token:
        return

    payload: dict = {"secret": secret, "response": token}
    if ip:
        payload["remoteip"] = ip

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(VERIFY_URL, data=payload)
            resp.raise_for_status()
            result = resp.json()
    except httpx.HTTPError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not reach CAPTCHA verification service. Please try again.",
        )

    if not result.get("success"):
        # token-already-seen / timeout-or-duplicate → ask user to retry
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CAPTCHA verification failed. Please complete the challenge and try again.",
        )
