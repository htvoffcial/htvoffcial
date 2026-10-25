#!/usr/bin/env python3
"""Post the DISCUSS_COACH (体操のお兄さん) section of README.md to Nostr.

Used by .github/workflows/nostr-daily.yml (daily 13:00 JST).

How it works:
  1. Extract the text between <!-- DISCUSS_COACH_START --> and
     <!-- DISCUSS_COACH_END --> in README.md.
  2. Clean the Markdown (headings / bold) into plain text suitable for Nostr.
  3. Before posting, query the relays for this account's recent kind-1
     notes; if the exact same text was already posted, skip (no duplicates,
     even if the workflow runs twice or README did not change that day).
  4. Sign with the key from the NOSTR_NSEC environment variable
     (a GitHub Actions Secret) and publish to the relays below.

Required environment:
  NOSTR_NSEC   nsec1... secret key of the dedicated GitHub Actions account.
               Never commit this to the repository.

Usage:
  python .github/scripts/post_to_nostr.py [--readme README.md] [--dry-run] [--force]
    --dry-run  Build, sign and dedup-check, but do not publish.
    --force    Post even if the same text is already on the relays.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time

from pynostr.event import Event, EventKind
from pynostr.key import PrivateKey
import websocket

RELAYS = [
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://nostr.mom",
    "wss://relay.nostr.net",
    "wss://yabu.me",
    "wss://relay.snort.social",
    "wss://nostr-relay.haruharutv.jp",
]

START_MARKER = "<!-- DISCUSS_COACH_START -->"
END_MARKER = "<!-- DISCUSS_COACH_END -->"
SOURCE_URL = "https://conversations.harutv.me/"


def extract_section(readme_text: str) -> str:
    if START_MARKER not in readme_text or END_MARKER not in readme_text:
        raise SystemExit("ERROR: DISCUSS_COACH markers not found in README.md")
    section = readme_text.split(START_MARKER, 1)[1].split(END_MARKER, 1)[0].strip()
    if not section:
        raise SystemExit("ERROR: DISCUSS_COACH section is empty")
    return section


def build_post(section: str) -> dict:
    """Turn the Markdown section into a plain-text Nostr post."""
    target_date = None
    m = re.search(r"対象日（JST）[:：]\**\s*(\d{4}-\d{2}-\d{2})", section)
    if m:
        target_date = m.group(1)

    lines = []
    for raw in section.splitlines():
        line = raw.strip()
        # Drop the title heading and the date line; they are rebuilt below.
        if line.startswith("#"):
            continue
        if re.match(r"\**対象日（JST）", line):
            continue
        # Strip bold markers for plain-text Nostr clients.
        line = line.replace("**", "")
        lines.append(line)

    body = "\n".join(lines)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if not body:
        raise SystemExit("ERROR: message body is empty after cleaning")

    header = "Discussまとめ（体操のお兄さん）"
    if target_date:
        header += f"\n対象日（JST）: {target_date}"
    text = f"{header}\n\n{body}\n\n{SOURCE_URL}\n#はるはる体操のお兄さん"
    return {
        "text": text,
        "target_date": target_date,
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def fetch_own_notes(pubkey_hex: str, limit: int = 50) -> tuple[list, list]:
    """Return (events, relays_that_answered) from the relays."""
    events_by_id = {}
    answered = []
    req = json.dumps(
        ["REQ", "dedup-check", {"authors": [pubkey_hex], "kinds": [1], "limit": limit}],
        ensure_ascii=False,
    )
    for relay in RELAYS:
        got = []
        try:
            ws = websocket.create_connection(relay, timeout=12)
            try:
                ws.send(req)
                ws.settimeout(8)
                deadline = time.time() + 8
                while time.time() < deadline:
                    data = json.loads(ws.recv())
                    if isinstance(data, list) and data and data[0] == "EVENT":
                        got.append(data[2])
                    if isinstance(data, list) and data and data[0] == "EOSE":
                        break
            finally:
                ws.close()
        except Exception as e:
            print(f"dedup query {relay}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        answered.append(relay)
        for ev in got:
            events_by_id[ev["id"]] = ev
    return list(events_by_id.values()), answered


def publish(event: Event, key: PrivateKey) -> dict:
    event.sign(key.raw_secret.hex() if isinstance(key.raw_secret, bytes) else bytes(key.raw_secret).hex())
    payload = json.dumps(["EVENT", event.to_dict()], ensure_ascii=False)
    results = {}
    for relay in RELAYS:
        ok, msg = False, ""
        try:
            ws = websocket.create_connection(relay, timeout=12)
            try:
                ws.send(payload)
                ws.settimeout(12)
                deadline = time.time() + 12
                while time.time() < deadline:
                    data = json.loads(ws.recv())
                    if isinstance(data, list) and data and data[0] == "OK" and data[1] == event.id:
                        ok, msg = bool(data[2]), str(data[3] if len(data) > 3 else "")
                        break
                    if isinstance(data, list) and data and data[0] == "NOTICE":
                        msg = str(data[1] if len(data) > 1 else "")
            finally:
                ws.close()
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
        results[relay] = {"ok": ok, "message": msg}
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--readme", default="README.md")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    with open(args.readme, "r", encoding="utf-8") as f:
        section = extract_section(f.read())
    post = build_post(section)
    print("----- post text -----")
    print(post["text"])
    print("---------------------")
    print(f"target_date={post['target_date']} content_hash={post['content_hash']}")

    nsec = os.environ.get("NOSTR_NSEC", "").strip()
    if not nsec:
        if args.dry_run:
            print("DRY-RUN: NOSTR_NSEC not set, skipping sign/dedup/publish.")
            return 0
        print("ERROR: NOSTR_NSEC is not set (add it as a repository Secret).", file=sys.stderr)
        return 1
    key = PrivateKey.from_nsec(nsec)
    pubkey_hex = key.public_key.hex()

    # Duplicate check: never post the exact same text twice.
    if not args.force:
        notes, answered = fetch_own_notes(pubkey_hex)
        if not answered:
            print("ERROR: no relay answered the dedup check; aborting to avoid a duplicate post.", file=sys.stderr)
            return 1
        for ev in notes:
            if ev.get("content", "").strip() == post["text"].strip():
                print(json.dumps({
                    "status": "skipped",
                    "reason": "same content already posted",
                    "existing_event_id": ev["id"],
                    "target_date": post["target_date"],
                    "content_hash": post["content_hash"],
                }, ensure_ascii=False))
                return 0

    tags = [
        ["client", "github-actions-nostr-daily"],
        ["r", "https://github.com/htvoffcial/htvoffcial"],
        ["t", "はるはる体操のお兄さん"],
    ]
    if post["target_date"]:
        tags.append(["t", f"discuss-coach-{post['target_date']}"])

    event = Event(content=post["text"], kind=EventKind.TEXT_NOTE, tags=tags)

    if args.dry_run:
        # Sign locally to prove the key works, but publish nothing.
        event.sign(key.raw_secret.hex() if isinstance(key.raw_secret, bytes) else bytes(key.raw_secret).hex())
        print(json.dumps({
            "status": "dry-run",
            "would_post_event_id": event.id,
            "note": event.bech32(),
            "target_date": post["target_date"],
            "content_hash": post["content_hash"],
        }, ensure_ascii=False))
        return 0

    results = publish(event, key)
    relays_ok = [r for r, v in results.items() if v["ok"]]
    print(json.dumps({
        "status": "posted" if relays_ok else "failed",
        "event_id": event.id,
        "note": event.bech32(),
        "njump": f"https://njump.me/{event.bech32()}",
        "target_date": post["target_date"],
        "content_hash": post["content_hash"],
        "relays_ok": relays_ok,
        "relays": results,
    }, ensure_ascii=False, indent=2))
    return 0 if relays_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
