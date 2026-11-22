"""Why won't a website load? Look at its HTTPS certificate directly.

Called only after the page fetch failed, so a dead site is reported as what it is:

    valid        the certificate checks out; the fetch failed for some other reason
    expired      the certificate's end date has passed (a site nobody renews is not being run)
    invalid      another certificate problem (self-signed, wrong hostname, unknown issuer)
    unavailable  could not connect at all (timeout, refused, DNS). Slow is not missing: this is
                 never read as a finding about the vendor.

Free (no API), one TLS handshake, a few seconds at most.
"""
import os
import socket
import ssl
import tempfile
from datetime import datetime, timezone

TIMEOUT = 8
EXPIRED_CODE = 10  # X509_V_ERR_CERT_HAS_EXPIRED


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _decode_der(der):
    """notAfter/issuer of a certificate we refused to trust, using only the standard library."""
    pem = ssl.DER_cert_to_PEM_cert(der)
    fd, path = tempfile.mkstemp(suffix=".pem")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(pem)
        return ssl._ssl._test_decode_cert(path)  # noqa: SLF001 (stdlib, no extra dependency)
    except Exception:  # noqa: BLE001 - the date is a nice-to-have, never required
        return {}
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _iso(not_after):
    if not not_after:
        return None
    try:
        return datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), timezone.utc).date().isoformat()
    except (ValueError, OverflowError):
        return None


def _issuer(cert):
    for rdn in cert.get("issuer") or ():
        for k, v in rdn:
            if k == "organizationName":
                return v
    return None


def inspect(host, port=443):
    out = {"host": host, "checked_at": _now()}
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT) as sock:
            with ssl.create_default_context().wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
        return {**out, "status": "valid", "not_after": _iso(cert.get("notAfter")), "issuer": _issuer(cert)}
    except ssl.SSLCertVerificationError as e:
        problem = e.verify_message or str(e)
        status = "expired" if e.verify_code == EXPIRED_CODE else "invalid"
    except (OSError, ssl.SSLError) as e:
        return {**out, "status": "unavailable", "reason": f"{type(e).__name__}: {str(e)[:150]}"}

    # The certificate was refused: fetch it again without trusting it, only to read its dates.
    cert = {}
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((host, port), timeout=TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
        cert = _decode_der(der) if der else {}
    except (OSError, ssl.SSLError):
        pass
    not_after = _iso(cert.get("notAfter"))
    # OpenSSL reports the first problem it hits: a server that also stopped sending its chain says
    # "unable to get local issuer certificate" even when the certificate itself has expired.
    if not_after and not_after < datetime.now(timezone.utc).date().isoformat():
        status = "expired"
    return {**out, "status": status, "problem": problem, "not_after": not_after, "issuer": _issuer(cert)}
