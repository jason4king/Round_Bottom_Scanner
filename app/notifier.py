from __future__ import annotations

import json
import urllib.error
import urllib.request


def send_wecom_text(webhook_url: str, content: str, timeout: float = 10.0) -> None:
    """Post a plain-text message to a WeCom (企业微信) group robot webhook."""
    payload = json.dumps({"msgtype": "text", "text": {"content": content}}).encode("utf-8")
    request = urllib.request.Request(
        webhook_url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    if body.get("errcode") != 0:
        raise RuntimeError(f"WeCom webhook rejected message: {body}")
