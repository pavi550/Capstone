"""Run an operator-supplied application plan inside the isolated verifier container."""

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def command(value):
    if (not isinstance(value, list) or not value
            or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in value)
            or not value[0].startswith("/")):
        raise ValueError("DEV commands require an absolute executable and explicit arguments")
    return value


def probe(opener, check):
    url = check["url"]
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}
            or parsed.username is not None or parsed.fragment or not parsed.port):
        raise ValueError("DEV probes must use a loopback HTTP URL with an explicit port")
    try:
        response = opener.open(url, timeout=2)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        if response.status != check.get("status", 200):
            return False
        if "contains" not in check:
            return True
        body = response.read(32769)
        return len(body) <= 32768 and check["contains"].encode() in body


def execute(plan):
    timeout = plan.get("timeout", 45)
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise ValueError("DEV timeout must be an integer from 1 to 300 seconds")
    deadline = time.monotonic() + timeout
    build = plan.get("build", [])
    smoke = plan.get("smoke", [])
    if not isinstance(build, list) or not isinstance(smoke, list) or len(build) > 10 or len(smoke) > 10:
        raise ValueError("DEV plans support up to ten build commands and ten smoke probes")
    for argv in build:
        subprocess.run(command(argv), check=True, timeout=max(0.01, deadline - time.monotonic()))  # nosec B603
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    service = subprocess.Popen(command(plan["start"]), stdin=subprocess.DEVNULL)  # nosec B603
    try:
        while time.monotonic() < deadline:
            if service.poll() is not None:
                raise RuntimeError("Application exited before becoming ready")
            try:
                if probe(opener, plan["health"]):
                    break
            except (TimeoutError, urllib.error.URLError):
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("Application health deadline exceeded")
        for check in smoke:
            if time.monotonic() >= deadline or not probe(opener, check):
                raise RuntimeError("Application smoke probe failed")
        print(json.dumps({"health": "PASSED", "smoke_probes": len(smoke), "build_steps": len(build)}))
    finally:
        if service.poll() is None:
            service.terminate()
            try:
                service.wait(timeout=3)
            except subprocess.TimeoutExpired:
                service.kill()
                service.wait()


if __name__ == "__main__":
    try:
        execute(json.loads(sys.argv[1]))
    except (KeyError, OSError, RuntimeError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"DEV harness failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
