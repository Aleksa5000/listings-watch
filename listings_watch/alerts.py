"""Webhook alert, Slack- and Discord-compatible. Skipped silently when no URL is configured."""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger("listings_watch")


def send_alert(
    webhook_url: str | None, text: str, *, transport: httpx.BaseTransport | None = None
) -> bool:
    """POST ``text`` to the webhook. Returns True if it was delivered; never raises."""
    if not webhook_url:
        return False
    text = text[:1900]  # Discord rejects messages over 2000 characters
    try:
        with httpx.Client(timeout=10, transport=transport) as client:
            # Slack reads "text", Discord reads "content"; each ignores the other key.
            response = client.post(webhook_url, json={"text": text, "content": text})
            response.raise_for_status()
    except httpx.HTTPError as exc:
        log.error("alert webhook failed: %s", type(exc).__name__)  # URL is a secret: never log it
        return False
    return True
