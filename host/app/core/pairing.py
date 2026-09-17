import secrets
import socket
from fastapi import Header, HTTPException, Request
from app.core.config import settings


def get_pair_token() -> str:
    """Token the phone must send as X-Pair-Token. Created once and kept in the data dir."""
    path = settings.pair_token_path
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(24)
    path.write_text(token, encoding="utf-8")
    return token


def lan_ipv4_addresses() -> list[str]:
    """Every IPv4 address the phone might reach this PC on, default-route interface first."""
    candidates = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            # UDP connect sends no packets; it only asks the OS which interface would be used.
            s.connect(("10.255.255.255", 1))
            candidates.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidates.append(info[4][0])
    except socket.gaierror:
        pass

    ips: list[str] = []
    for ip in candidates:
        if ip.startswith(("127.", "169.254.", "0.")) or ip in ips:
            continue
        ips.append(ip)
    return ips


def _is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host == "::1" or host == "localhost" or host.startswith("127.")


def require_local(request: Request) -> None:
    if not _is_loopback(request):
        raise HTTPException(403, "Only available from this PC")


def require_phone_or_local(request: Request, x_pair_token: str | None = Header(default=None)) -> None:
    """The viewer (same PC) needs no token; anything arriving over the LAN must be the paired phone."""
    if _is_loopback(request):
        return
    if x_pair_token and secrets.compare_digest(x_pair_token, get_pair_token()):
        return
    raise HTTPException(401, "Invalid or missing pair token")
