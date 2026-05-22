"""
HTTPS / TLS for Sentinel Hub and Copernicus (CDSE) clients.

Corporate VPN / SSL inspection often breaks Python's default certifi bundle.
Call ``configure_sh_http()`` before importing ``sentinelhub`` — typically from
``sentinel_client`` / ``statistical_client`` or at the start of batch scripts.

Priority:
1. If ``SH_VERIFY_SSL`` is ``false`` / ``0`` / ``no``: disable TLS verification
   for ``requests`` (last resort; logs a warning).
2. Else if ``REQUESTS_CA_BUNDLE`` or ``SSL_CERT_FILE`` is set: use that PEM (IT bundle).
3. Else try ``truststore.inject_into_ssl()`` so Python uses the OS trust store
   (Windows: includes enterprise roots pushed by IT).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_done = False
_requests_verify_patch_done = False


def _truthy(val: str | None) -> bool:
    if val is None:
        return False
    return val.strip().lower() in ("1", "true", "yes", "on")


def _patch_requests_verify_disabled() -> None:
    global _requests_verify_patch_done
    if _requests_verify_patch_done:
        return
    _requests_verify_patch_done = True

    import warnings

    import urllib3
    from urllib3.exceptions import InsecureRequestWarning

    urllib3.disable_warnings(InsecureRequestWarning)

    import requests

    _orig = requests.sessions.Session.request

    def _request(self, method, url, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("verify", False)
        return _orig(self, method, url, **kwargs)

    requests.sessions.Session.request = _request  # type: ignore[method-assign]
    warnings.warn(
        "SH_VERIFY_SSL=false: TLS verification is disabled for HTTPS (including Sentinel Hub). "
        "Use only on trusted networks.",
        UserWarning,
        stacklevel=2,
    )


def configure_sh_http() -> None:
    """Configure TLS for Copernicus / Sentinel Hub HTTP clients. Safe to call repeatedly."""
    global _done
    if _done:
        return
    _done = True

    if _truthy(os.environ.get("SH_SSL_DISABLED", "")) or not _truthy(
        os.environ.get("SH_VERIFY_SSL", "true")
    ):
        logger.warning(
            "SH_VERIFY_SSL is disabled: Copernicus/Sentinel Hub requests will not verify TLS certificates."
        )
        _patch_requests_verify_disabled()
        return

    if os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE"):
        logger.info("Using REQUESTS_CA_BUNDLE / SSL_CERT_FILE for HTTPS trust.")
        return

    try:
        import truststore

        truststore.inject_into_ssl()
        logger.info("Using system certificate store for HTTPS (truststore).")
    except ImportError:
        logger.debug(
            "Install 'truststore' (pip install truststore) to use the Windows/macOS trust store on corporate VPN."
        )
    except Exception as e:
        logger.warning("truststore.inject_into_ssl() failed: %s", e)
