"""mDNS advertisement and peer discovery for zero-config REACHER setup.

Soft dependency on ``zeroconf`` — the module degrades gracefully when the
library is not installed, logging an info message and disabling mDNS.

Devices advertise themselves as ``_reacher._tcp.local.`` with TXT records
containing ``device_id`` and ``version``.  No API key is ever broadcast.
"""

import asyncio
import logging
import socket
import threading

logger = logging.getLogger(__name__)

_SERVICE_TYPE = "_reacher._tcp.local."
_peers: dict[str, dict] = {}  # {device_id → {host, port, hostname}}
_peers_lock = threading.Lock()

_scanned_peers: dict[str, dict] = {}  # {device_id → {host, port, hostname}}
_scanned_lock = threading.Lock()

# Peers that self-registered via POST /api/discovery/register (unicast fallback for
# networks where mDNS multicast is blocked, e.g. university managed switches).
_registered_peers: dict[str, dict] = {}  # {device_id → {host, port, hostname}}
_registered_lock = threading.Lock()

# Module-level zeroconf state (None when disabled or not started)
_zeroconf = None
_browser = None
_info = None


class _ServiceListener:
    """Handles mDNS service add/remove callbacks for the ServiceBrowser."""

    def __init__(self, own_device_id: str) -> None:
        self._own_device_id = own_device_id

    def add_service(self, zc, service_type: str, name: str) -> None:  # noqa: ANN001
        try:
            info = zc.get_service_info(service_type, name)
            if not info:
                return
            props = {k.decode(): v.decode() for k, v in info.properties.items()}
            device_id = props.get("device_id")
            # Skip self — the machine picks up its own mDNS advertisement via ServiceBrowser.
            # Without this guard the local device_id would appear in _peers and surface in
            # GET /api/discovery as an unpaired "discovered" device.
            if not device_id or device_id == self._own_device_id:
                return
            addresses = info.parsed_addresses()
            if not addresses:
                return
            host = addresses[0]
            hostname = info.server.rstrip(".") if info.server else host
            with _peers_lock:
                _peers[device_id] = {"host": host, "port": info.port, "hostname": hostname}
            logger.debug("Discovered REACHER peer: %s @ %s:%d", device_id[:8], host, info.port)
        except Exception:
            logger.debug("Failed to parse mDNS service info for %s", name)

    def remove_service(self, zc, service_type: str, name: str) -> None:  # noqa: ANN001
        # Service name format: REACHER-{device_id[:8]}._reacher._tcp.local.
        try:
            prefix = name.split(".")[0]  # e.g. "REACHER-abcd1234"
            if not prefix.startswith("REACHER-"):
                return
            short_id = prefix[len("REACHER-"):]
            with _peers_lock:
                to_remove = [d for d in list(_peers) if d.startswith(short_id)]
                for d in to_remove:
                    del _peers[d]
        except Exception:
            pass

    def update_service(self, zc, service_type: str, name: str) -> None:  # noqa: ANN001
        self.add_service(zc, service_type, name)


def start(device_id: str, port: int, version: str) -> None:
    """Register this device via mDNS and start browsing for peers.

    Blocking (~100 ms for service registration); intended to be called via
    ``asyncio.get_event_loop().run_in_executor(None, discovery.start, ...)``.

    No-op if the ``zeroconf`` package is not installed.
    """
    global _zeroconf, _browser, _info

    try:
        from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf
    except ImportError:
        logger.warning("zeroconf not installed — mDNS device discovery disabled; install with: pip install zeroconf")
        return

    try:
        _zeroconf = Zeroconf()

        # Resolve local IP for advertisement (prefer non-loopback).
        # socket.gethostbyname(socket.gethostname()) commonly returns 127.0.0.1
        # or 127.0.1.1 on Linux/Raspberry Pi due to /etc/hosts entries, which
        # would make the mDNS advertisement unreachable from other machines.
        # _get_local_ip() uses the routing table (UDP socket trick) instead.
        local_ip = _get_local_ip() or "127.0.0.1"

        addr_bytes = socket.inet_aton(local_ip)
        service_name = f"REACHER-{device_id[:8]}.{_SERVICE_TYPE}"

        _info = ServiceInfo(
            _SERVICE_TYPE,
            service_name,
            addresses=[addr_bytes],
            port=port,
            properties={
                b"device_id": device_id.encode(),
                b"version": version.encode(),
            },
            server=f"{socket.gethostname()}.local.",
        )
        _zeroconf.register_service(_info)
        logger.info("Registered mDNS service: %s", service_name)

        _browser = ServiceBrowser(_zeroconf, _SERVICE_TYPE, _ServiceListener(device_id))
    except Exception:
        logger.exception("Failed to start mDNS discovery")


def stop() -> None:
    """Unregister the mDNS service and shut down the browser.

    Blocking (~100 ms); intended to be called via run_in_executor.
    """
    global _zeroconf, _browser, _info

    if _zeroconf is None:
        return
    try:
        if _info is not None:
            _zeroconf.unregister_service(_info)
        _zeroconf.close()
    except Exception:
        logger.debug("Error stopping mDNS", exc_info=True)
    finally:
        _zeroconf = None
        _browser = None
        _info = None


def get_peers() -> dict[str, dict]:
    """Return a point-in-time snapshot of currently visible REACHER peers.

    Merges all three discovery sources; precedence (highest wins on conflict):
      mDNS > subnet-scan > unicast-registered

    Returns:
        Dict mapping device_id → {host, port, hostname}.
    """
    with _registered_lock:
        registered = dict(_registered_peers)
    with _scanned_lock:
        scanned = dict(_scanned_peers)
    with _peers_lock:
        mdns = dict(_peers)
    return {**registered, **scanned, **mdns}


def register_peer(device_id: str, host: str, port: int, hostname: str) -> None:
    """Store a peer that unicast-registered via POST /api/discovery/register.

    Used on networks where mDNS multicast is unavailable (e.g. university
    managed switches with AP client isolation).  Peripheral devices call this
    endpoint on startup when ``REACHER_BROKER_URL`` is configured.
    """
    with _registered_lock:
        _registered_peers[device_id] = {"host": host, "port": port, "hostname": hostname}
    logger.info("Registered peer via unicast: %s @ %s:%d", device_id[:8], host, port)


def _get_local_ip() -> str | None:
    """Return the primary outbound IPv4 address of this machine.

    Used for mDNS advertisement and broker registration where a single
    representative IP is needed.  For subnet scanning, use
    ``_get_all_local_ips()`` instead.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # no traffic is sent
            return s.getsockname()[0]
    except Exception:
        return None


def _get_all_local_ips() -> list[str]:
    """Return all non-loopback IPv4 addresses on this machine.

    Uses ``fcntl.ioctl(SIOCGIFADDR)`` per interface on Linux, falling
    back to ``_get_local_ip()`` on failure.
    """
    try:
        import fcntl
        import struct

        SIOCGIFADDR = 0x8915
        ips: list[str] = []
        for _idx, name in socket.if_nameindex():
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    result = fcntl.ioctl(
                        s.fileno(),
                        SIOCGIFADDR,
                        struct.pack("256s", name.encode("utf-8")[:15]),
                    )
                ip_str = socket.inet_ntoa(result[20:24])
                if not ip_str.startswith("127."):
                    ips.append(ip_str)
            except OSError:
                continue
        if ips:
            return ips
    except Exception:
        logger.debug("Interface enumeration failed, falling back", exc_info=True)

    ip = _get_local_ip()
    return [ip] if ip else []


_SCAN_CONCURRENCY = 50


async def scan_once(http_client, port: int, own_device_id: str) -> None:  # noqa: ANN001
    """Probe every host on all local /24 subnets for REACHER devices.

    Results are stored in ``_scanned_peers`` and merged into ``get_peers()``.
    Skips ``own_device_id`` to avoid self-discovery.
    """
    local_ips = _get_all_local_ips()
    if not local_ips:
        logger.debug("No local IPs found — skipping subnet scan")
        return

    prefixes: set[str] = set()
    for ip in local_ips:
        prefixes.add(".".join(ip.split(".")[:3]))

    own_ips = set(local_ips)
    hosts = [
        f"{prefix}.{i}"
        for prefix in sorted(prefixes)
        for i in range(1, 255)
        if f"{prefix}.{i}" not in own_ips
    ]

    logger.info("Subnet scan: probing %d hosts across %d subnet(s)", len(hosts), len(prefixes))
    semaphore = asyncio.Semaphore(_SCAN_CONCURRENCY)

    async def probe(host: str):
        async with semaphore:
            try:
                resp = await http_client.get(f"http://{host}:{port}/health", timeout=1.5)
                h = resp.json()
                if h.get("service") == "reacher" and h.get("device_id") != own_device_id:
                    return h["device_id"], {
                        "host": host,
                        "port": port,
                        "hostname": h.get("hostname", host),
                    }
            except Exception:
                pass

    results = await asyncio.gather(*[probe(h) for h in hosts])
    found = {r[0]: r[1] for r in results if r}
    with _scanned_lock:
        _scanned_peers.clear()
        _scanned_peers.update(found)
    if found:
        logger.info("Subnet scan found %d peer(s)", len(found))
    else:
        logger.debug("Subnet scan found no peers")


async def run_scan_loop(http_client, port: int, own_device_id: str) -> None:  # noqa: ANN001
    """Run ``scan_once`` immediately then every 30 seconds until cancelled."""
    while True:
        try:
            await scan_once(http_client, port, own_device_id)
        except Exception:
            logger.debug("Subnet scan error", exc_info=True)
        await asyncio.sleep(30)
