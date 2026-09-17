import html
import json
import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from app.core.config import settings
from app.core.pairing import get_pair_token, lan_ipv4_addresses, require_local

router = APIRouter(prefix="/api", dependencies=[Depends(require_local)])


def pairing_payload() -> dict:
    # Every LAN address goes in the QR code: the PC's IP differs between campus Wi-Fi and a phone hotspot.
    return {"ips": lan_ipv4_addresses(), "port": settings.port, "token": get_pair_token()}


@router.get("/pair/info")
def pair_info():
    return pairing_payload()


@router.get("/pair", response_class=HTMLResponse)
def pair_page():
    payload = pairing_payload()
    svg = qrcode.make(
        json.dumps(payload, separators=(",", ":")),
        image_factory=qrcode.image.svg.SvgPathImage,
        box_size=12,
    ).to_string(encoding="unicode")

    ips = ", ".join(payload["ips"]) or "none found - is this PC on a network?"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pair phone</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #0F172A; color: #E2E8F0; margin: 0; padding: 32px 16px; }}
  main {{ max-width: 460px; margin: 0 auto; }}
  .qr {{ background: #fff; padding: 16px; border-radius: 12px; }}
  .qr svg {{ width: 100%; height: auto; display: block; }}
  dt {{ color: #94A3B8; font-size: 13px; margin-top: 12px; }}
  dd {{ margin: 2px 0 0; font-family: ui-monospace, monospace; word-break: break-all; }}
  p {{ color: #94A3B8; font-size: 14px; line-height: 1.5; }}
</style>
</head>
<body>
<main>
  <h1>Pair your phone</h1>
  <p>In the phone app, tap <b>Pair PC</b> and scan this code.</p>
  <div class="qr">{svg}</div>
  <h2>Manual entry</h2>
  <dl>
    <dt>IP address</dt><dd>{html.escape(ips)}</dd>
    <dt>Port</dt><dd>{payload["port"]}</dd>
    <dt>Pair token</dt><dd>{html.escape(payload["token"])}</dd>
  </dl>
  <p>If the phone can't connect, allow inbound TCP port {payload["port"]} for Python in Windows Defender Firewall,
  and check the phone and PC are on the same Wi-Fi or hotspot.</p>
</main>
</body>
</html>"""
