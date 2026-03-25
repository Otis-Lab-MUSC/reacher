"""mDNS advertisement and peer discovery for zero-config REACHER setup.

Soft dependency on ``zeroconf`` — the module degrades gracefully when the
library is not installed, logging an info message and disabling mDNS.

Devices advertise themselves as ``_reacher._tcp.local.`` with TXT records
containing ``device_id`` and ``version``.  No API key is ever broadcast.
"""

import logging
import socket
import threading

logger = logging.getLogger(__name__)

_SERVICE_TYPE = "_reacher._tcp.local."
_peers: dict[str, dict] = {}  # {device_id → {host, port, hostname}}
_peers_lock = threading.Lock()

# Module-level zeroconf state (None when disabled or not started)
_zeroconf = None
_browser = None
_info = None


class _ServiceListener:
    """Handles mDNS service add/remove callbacks for the ServiceBrowser."""

    def add_service(self, zc, service_type: str, name: str) -> None:  # noqa: ANN001
        try:
            info = zc.get_service_info(service_type, name)
            if not info:
                return
            props = {k.decode(): v.decode() for k, v in info.properties.items()}
            device_id = props.get("device_id")
            if not device_id:
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
        logger.info("zeroconf not installed — mDNS device discovery disabled")
        return

    try:
        _zeroconf = Zeroconf()

        # Resolve local IP for advertisement (prefer non-loopback)
        try:
            local_ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            local_ip = "127.0.0.1"

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

        _browser = ServiceBrowser(_zeroconf, _SERVICE_TYPE, _ServiceListener())
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

    Returns:
        Dict mapping device_id → {host, port, hostname}.
    """
    with _peers_lock:
        return dict(_peers)
