#!/usr/bin/env python3
"""Connect to a free Japanese VPN Gate server via OpenVPN (best effort).

Used by .github/workflows/nostr-daily.yml before posting to Nostr, so the
post leaves from a Japanese IP address (some relays, e.g. yabu.me, drop
writes from non-Japanese IPs).

Behaviour:
  - Downloads the public VPN Gate server list, picks Japanese servers by
    Score, and tries to connect to each in turn (up to MAX_ATTEMPTS).
  - After each attempt, verifies the exit IP's country via ipinfo.io.
  - On success, leaves OpenVPN running in the background and exits 0.
  - On total failure, exits 0 as well (with a warning) so the workflow can
    continue without VPN; posting to the other relays still works.

Only signed, public Nostr events traverse the VPN; the secret key never
leaves the Actions runner.

Note: the OpenVPN log file is created by root (via sudo) with restrictive
permissions, so it is read back through `sudo cat`, never opened directly.
"""
import base64
import csv
import io
import os
import subprocess
import sys
import time
import urllib.request

LIST_URLS = [
    "https://www.vpngate.net/api/iphone/",
    "http://www.vpngate.net/api/iphone/",
]
MAX_ATTEMPTS = 6
# Server that worked on 2026-10-29; always tried first. If it disappears
# from the VPN Gate list or is unreachable, fall back to Score order.
PINNED = [("public-vpn-155", "219.100.37.110")]
CONNECT_WAIT_SEC = 45
OVPN_PATH = "/tmp/vpngate.ovpn"
LOG_PATH = "/tmp/openvpn.log"


def fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read().decode("utf-8", "replace")


def kill_openvpn() -> None:
    subprocess.run(["sudo", "pkill", "openvpn"], capture_output=True)
    time.sleep(2)


def read_log() -> str:
    try:
        r = subprocess.run(["sudo", "cat", LOG_PATH],
                           capture_output=True, text=True, timeout=10)
        return r.stdout or ""
    except Exception:
        return ""


COUNTRY_CHECK_URLS = [
    "https://ipinfo.io/country",
    "https://ipapi.co/country/",
    "https://ifconfig.co/country-iso",
]


def exit_country() -> str:
    # The lookup services sometimes refuse or rate-limit requests coming
    # from shared VPN exits (403/429); try several before giving up.
    for url in COUNTRY_CHECK_URLS:
        try:
            return fetch(url, timeout=15).strip()
        except Exception as e:
            print(f"  country check via {url} failed: {e}")
    return ""


def try_connect(row) -> bool:
    host = row.get("#HostName") or row.get("HostName") or "?"
    ip = row.get("IP") or "?"
    cfg = base64.b64decode(row["OpenVPN_ConfigData_Base64"]).decode("utf-8", "replace")
    # VPN Gate configs do not redirect the default gateway; add it so all
    # traffic (including the Nostr relay connections) uses the tunnel.
    cfg += "\nredirect-gateway def1\n"
    with open(OVPN_PATH, "w") as f:
        f.write(cfg)
    subprocess.run(["sudo", "rm", "-f", LOG_PATH], capture_output=True)
    print(f"Trying {host} ({ip}) ...")
    subprocess.run(
        ["sudo", "openvpn", "--config", OVPN_PATH, "--connect-timeout", "20",
         "--daemon", "--log", LOG_PATH, "--writepid", "/tmp/openvpn.pid"],
        capture_output=True,
    )
    deadline = time.time() + CONNECT_WAIT_SEC
    while time.time() < deadline:
        log = read_log()
        if "AUTH_FAILED" in log or "Exiting due to fatal error" in log:
            print("  fatal error in openvpn log")
            return False
        if "Initialization Sequence Completed" in log:
            country = exit_country()
            print(f"  connected; exit country = {country or 'unknown'}")
            return country == "JP"
        time.sleep(3)
    # Fallback: even if the log could not be read, a Japanese exit IP
    # means the tunnel is up and doing what we need.
    country = exit_country()
    if country == "JP":
        print("  connected (confirmed by exit country check)")
        return True
    print("  timed out")
    return False


def main() -> int:
    csv_text = None
    for url in LIST_URLS:
        try:
            csv_text = fetch(url)
            break
        except Exception as e:
            print(f"server list fetch failed ({url}): {e}")
    if not csv_text:
        print("::warning::VPN Gate server list unavailable; continuing without VPN")
        return 0

    lines = [l for l in csv_text.splitlines() if not l.startswith("*")]
    rows = list(csv.DictReader(io.StringIO("\n".join(lines))))

    def score(r) -> int:
        try:
            return int(r.get("Score") or 0)
        except ValueError:
            return 0

    jp = [r for r in rows
          if r.get("CountryShort") == "JP" and r.get("OpenVPN_ConfigData_Base64")]
    jp.sort(key=score, reverse=True)

    def is_pinned(r) -> bool:
        host = r.get("#HostName") or r.get("HostName") or ""
        return any(host == h or r.get("IP") == ip for h, ip in PINNED)

    pinned = [r for r in jp if is_pinned(r)]
    jp = pinned + [r for r in jp if not is_pinned(r)]
    print(f"Japanese VPN Gate candidates: {len(jp)} (pinned first: {len(pinned)})")

    for row in jp[:MAX_ATTEMPTS]:
        try:
            if try_connect(row):
                print("VPN Gate connected with a Japanese exit IP.")
                out = os.environ.get("GITHUB_OUTPUT")
                if out:
                    with open(out, "a") as f:
                        f.write("connected=true\n")
                return 0
        except Exception as e:
            print(f"  attempt failed: {e}")
        kill_openvpn()

    print("::warning::Could not establish a Japanese VPN Gate connection; "
          "continuing without VPN (yabu.me may drop the post)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
