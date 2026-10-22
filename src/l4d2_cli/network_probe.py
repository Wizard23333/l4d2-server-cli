"""Bounded network worker shared by source and frozen entry points."""
from __future__ import annotations

import json
import math
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

WORKER_FLAG = "--_l4d2-network-probe"


def check(kind: str, target: str, seconds: float) -> dict:
    try:
        if kind == "dns":
            addresses = sorted({item[4][0] for item in socket.getaddrinfo(target, None)})
            return {"status": "pass", "message": "DNS resolved", "addresses": addresses}
        parsed = urllib.parse.urlparse(target)
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
            raise ValueError("Invalid HTTP probe target")

        class Redirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                parsed = urllib.parse.urlparse(newurl)
                if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
                    raise ValueError("Unsafe redirect")
                if kind == "archive":
                    hosts = ("steamcdn-a.akamaihd.net", "media.steampowered.com", "cdn.cloudflare.steamstatic.com")
                    if parsed.scheme != "https" or parsed.hostname not in hosts:
                        raise ValueError("Unexpected archive redirect target")
                return super().redirect_request(req, fp, code, msg, headers, newurl)

        opener = urllib.request.build_opener(Redirect())
        request = urllib.request.Request(target, headers={"User-Agent": "l4d2-server-cli/0.1", "Range": "bytes=0-255"})
        with opener.open(request, timeout=seconds) as response:
            content = response.read(256)
            if kind == "archive" and not content.startswith(b"\x1f\x8b"):
                raise ValueError("Archive endpoint did not return gzip data (possible verification page)")
            if kind == "repository" and (b"<html" in content.lower() or b"<!doctype" in content.lower()):
                raise ValueError("Repository returned an HTML page")
            return {"status": "pass", "message": "HTTP endpoint reachable", "http_status": response.status}
    except urllib.error.HTTPError as exc:
        return {"status": "fail", "category": "http", "message": "HTTP " + str(exc.code)}
    except socket.gaierror:
        return {"status": "fail", "category": "dns", "message": "DNS resolution failed"}
    except (ssl.SSLError, ssl.CertificateError) as exc:
        return {"status": "fail", "category": "tls", "message": str(exc)}
    except (TimeoutError, socket.timeout):
        return {"status": "fail", "category": "timeout", "message": "Connection timed out"}
    except urllib.error.URLError as exc:
        reason = exc.reason
        category = "tls" if isinstance(reason, ssl.SSLError) else ("dns" if isinstance(reason, socket.gaierror) else "connection")
        return {"status": "fail", "category": category, "message": str(reason)}
    except Exception as exc:
        return {"status": "fail", "category": "response", "message": str(exc)}


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(args) != 3 or args[0] not in {"dns", "http", "archive", "repository"}:
            raise ValueError("Invalid probe arguments")
        seconds = float(args[2])
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Invalid probe timeout")
    except (ValueError, TypeError):
        print(json.dumps({"status": "fail", "category": "probe", "message": "Invalid network probe arguments"}))
        return 2
    print(json.dumps(check(args[0], args[1], seconds)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
