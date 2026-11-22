"""Run the real pipeline with a simulated outage, offline and with no credits.

    python tests/mock_run.py crustdata_timeout --name "Retool" --website "https://retool.com"

Runs in a fresh scratch directory (its own empty cache, job database and output), so the
outage can't be masked by cached responses and nothing touches the project's real state.
Every HTTP call is replaced by one that raises requests.Timeout, the backoff sleeps are
skipped, and everything else (retry logging, step bookkeeping, decide) runs unchanged.
Prints one JSON line: {"dir": <scratch dir>, "exit_code": <run.py exit code>}.
"""
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    mode, argv = sys.argv[1], sys.argv[2:]
    if mode != "crustdata_timeout":
        raise SystemExit(f"unknown mock {mode!r}")
    work = tempfile.mkdtemp(prefix="gatekeeper_mock_")
    shutil.copy(os.path.join(HERE, "config.json"), work)
    os.chdir(work)
    sys.path.insert(0, HERE)

    import requests

    def timeout(*_a, **_k):
        raise requests.Timeout("simulated: no answer within 60s")
    requests.post = timeout      # every Crustdata call (crust_client uses requests.post)
    requests.get = timeout       # and GDELT, so the test never touches the network
    time.sleep = lambda _s: None  # skip retry backoff; the retries themselves still run and are logged

    import run
    sys.argv = ["run.py", *argv]
    code = run.main()
    print(json.dumps({"dir": work, "exit_code": code}))


if __name__ == "__main__":
    main()
