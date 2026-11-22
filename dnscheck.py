"""Does a domain exist? DNS-over-HTTPS so the answer is explicit:

    exists       the resolver answered NOERROR (status 0)
    nxdomain     the resolver answered NXDOMAIN (status 3): no such domain. A definitive
                 "no record", not an outage.
    unavailable  timeout, connection error, SERVFAIL... after trying both resolvers. Slow is not
                 missing: this is never read as "the domain doesn't exist".

Every failed attempt is logged as a retry, like the Crustdata and GDELT clients.
"""
from datetime import datetime, timezone

import requests

import checks
import store

RESOLVERS = [
    ("https://dns.google/resolve", {}),
    ("https://cloudflare-dns.com/dns-query", {"accept": "application/dns-json"}),
]
TIMEOUT = 8


def lookup(host, job_id):
    reasons = []
    for i, (url, headers) in enumerate(RESOLVERS, start=1):
        try:
            r = requests.get(url, params={"name": host, "type": "A"}, headers=headers, timeout=TIMEOUT)
            r.raise_for_status()
            answer = r.json()
            kind = checks.classify_doh(answer)
            if kind != "error":
                return {"status": kind, "host": host, "resolver": url, "rcode": answer.get("Status"),
                        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            reason = f"DNS status {answer.get('Status')} from {url}"
        except (requests.RequestException, ValueError) as e:
            reason = f"{type(e).__name__} from {url}: {str(e)[:150]}"
        reasons.append(reason)
        last = i == len(RESOLVERS)
        store.log_retry(job_id, f"dns {host}", i, reason, 0, gave_up=last)
        print(f"    {reason[:100]} (DNS attempt {i}/{len(RESOLVERS)})" + ("; giving up" if last else ""))
    return {"status": "unavailable", "host": host, "reason": "; ".join(reasons),
            "retrieved_at": None}
