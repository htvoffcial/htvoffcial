#!/usr/bin/env python3
"""Post the DISCUSS_COACH (体操のお兄さん) section of README.md to Bluesky.

Used by .github/workflows/nostr-daily.yml (daily 13:00 JST), as a sibling
step to post_to_nostr.py. Only the Python standard library is used.

Bluesky specifics handled here:
  * A post is limited to 300 graphemes. The oniisan message is usually
    350-450 characters, so the text is split into a thread: chunks of at
    most 290 characters (counted in code points, which is conservative
    for graphemes), split on paragraph / sentence boundaries, posted as
    a reply chain so the full text is preserved.
  * Links and hashtags need rich-text facets with UTF-8 *byte* offsets,
    otherwise they render as plain text. Facets are built for the
    conversations.harutv.me link and the hashtag in whichever chunk
    they end up in.
  * Duplicate check: before posting, the account's recent feed is read
    and if the first chunk was already posted verbatim, everything is
    skipped (same policy as the Nostr script).

Required environment:
  BSKY_HANDLE        Bluesky handle (e.g. example.bsky.social).
  BSKY_APP_PASSWORD  An *app password* for that account (created in
                     Bluesky Settings > Privacy and security > App
                     passwords). Never commit it; use a GitHub Secret.
  BSKY_SERVICE       Optional PDS base URL, default https://bsky.social.

Usage:
  python .github/scripts/post_to_bluesky.py [--readme README.md]
                                            [--dry-run] [--force]
    --dry-run  Build chunks and facets and print them, but post nothing.
               Without credentials it stops after the preview.
    --force    Post even if the first chunk is already on the feed.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

START_MARKER = "<!-- DISCUSS_COACH_START -->"
END_MARKER = "<!-- DISCUSS_COACH_END -->"
SOURCE_URL = "https://conversations.harutv.me/"
HASHTAG = "#はるはる体操のお兄さん"
MAX_CHARS = 290  # stay safely under Bluesky's 300-grapheme limit
DEFAULT_SERVICE = "https://bsky.social"


# ---------------------------------------------------------------- text build

def extract_section(readme_text: str) -> str:
    if START_MARKER not in readme_text or END_MARKER not in readme_text:
        raise SystemExit("ERROR: DISCUSS_COACH markers not found in README.md")
    section = readme_text.split(START_MARKER, 1)[1].split(END_MARKER, 1)[0].strip()
    if not section:
        raise SystemExit("ERROR: DISCUSS_COACH section is empty")
    return section


def build_post(section: str) -> dict:
    """Same plain-text rendering as post_to_nostr.py, so both posts match."""
    target_date = None
    m = re.search(r"対象日（JST）[:：]\**\s*(\d{4}-\d{2}-\d{2})", section)
    if m:
        target_date = m.group(1)
    lines = []
    for raw in section.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        if re.match(r"\**対象日（JST）", line):
            continue
        lines.append(line.replace("**", ""))
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if not body:
        raise SystemExit("ERROR: message body is empty after cleaning")
    header = "Discussまとめ（体操のお兄さん）"
    if target_date:
        header += f"\n対象日（JST）: {target_date}"
    text = f"{header}\n\n{body}\n\n{SOURCE_URL}\n{HASHTAG}"
    return {
        "text": text,
        "target_date": target_date,
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def split_units(text: str) -> list:
    """Split text into (piece, separator_before) units.

    Boundaries are paragraph breaks first, then sentence-ending
    punctuation / line breaks inside a paragraph. The separator is
    "\\n\\n" for a new paragraph and "" for a continuation of the same
    paragraph (pieces carry their own ending punctuation / newline).
    A single over-long piece is hard-split.
    """
    units = []
    for p_idx, para in enumerate(text.split("\n\n")):
        if not para:
            continue
        pieces = [p for p in re.findall(r"[^。！？\n]*[。！？\n]?", para) if p]
        for i, piece in enumerate(pieces):
            sep = "\n\n" if (i == 0 and p_idx > 0) else ""
            if len(piece) <= MAX_CHARS:
                units.append((piece, sep))
            else:
                for j in range(0, len(piece), MAX_CHARS):
                    units.append((piece[j:j + MAX_CHARS], sep if j == 0 else ""))
    return units


def chunk_text(text: str) -> list:
    """Pack units into chunks of at most MAX_CHARS characters."""
    chunks = []
    current = ""
    for piece, sep in split_units(text):
        candidate = piece if not current else current + sep + piece
        if len(candidate) <= MAX_CHARS:
            current = candidate
            continue
        chunks.append(current.strip())
        current = piece
    if current.strip():
        chunks.append(current.strip())
    return chunks


def build_facets(text: str) -> list:
    facets = []
    for needle, feature in (
        (SOURCE_URL, {"$type": "app.bsky.richtext.facet#link", "uri": SOURCE_URL}),
        (HASHTAG, {"$type": "app.bsky.richtext.facet#tag", "tag": HASHTAG.lstrip("#")}),
    ):
        start = 0
        while True:
            idx = text.find(needle, start)
            if idx < 0:
                break
            byte_start = len(text[:idx].encode("utf-8"))
            byte_end = byte_start + len(needle.encode("utf-8"))
            facets.append({
                "$type": "app.bsky.richtext.facet",
                "index": {"byteStart": byte_start, "byteEnd": byte_end},
                "features": [feature],
            })
            start = idx + len(needle)
    facets.sort(key=lambda f: f["index"]["byteStart"])
    return facets


# ---------------------------------------------------------------- AT Protocol

def api(service: str, method: str, path: str, token: str = None, body: dict = None,
        params: dict = None) -> dict:
    url = service.rstrip("/") + "/xrpc/" + path
    if params:
        from urllib.parse import urlencode
        url += "?" + urlencode(params)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise SystemExit(f"ERROR: {path} failed: HTTP {e.code} {detail}")


def fetch_own_texts(service: str, token: str, did: str, limit: int = 30) -> list:
    out = api(service, "GET", "app.bsky.feed.getAuthorFeed", token=token,
              params={"actor": did, "limit": limit, "filter": "posts_no_replies"})
    texts = []
    for item in out.get("feed", []):
        rec = item.get("post", {}).get("record", {})
        if isinstance(rec.get("text"), str):
            texts.append(rec["text"])
    return texts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--readme", default="README.md")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    with open(args.readme, "r", encoding="utf-8") as f:
        post = build_post(extract_section(f.read()))
    chunks = chunk_text(post["text"])
    print(f"----- post text ({len(post['text'])} chars -> {len(chunks)} chunk(s)) -----")
    for i, chunk in enumerate(chunks, 1):
        print(f"[chunk {i} / {len(chunks)} : {len(chunk)} chars]")
        print(chunk)
        facets = build_facets(chunk)
        for fac in facets:
            b = chunk.encode("utf-8")[fac["index"]["byteStart"]:fac["index"]["byteEnd"]].decode("utf-8")
            print(f"  facet: {fac['features'][0]['$type'].rsplit('#', 1)[-1]} -> {b!r}")
    print("----------------------------------------------------------")
    print(f"target_date={post['target_date']} content_hash={post['content_hash']}")
    for chunk in chunks:
        if len(chunk) > 300:
            print("ERROR: a chunk exceeds 300 characters.", file=sys.stderr)
            return 1

    handle = os.environ.get("BSKY_HANDLE", "").strip()
    app_password = os.environ.get("BSKY_APP_PASSWORD", "").strip()
    service = os.environ.get("BSKY_SERVICE", DEFAULT_SERVICE).strip() or DEFAULT_SERVICE
    if args.dry_run:
        print("DRY-RUN: not posting.")
        if not (handle and app_password):
            return 0
    if not handle or not app_password:
        print("ERROR: BSKY_HANDLE / BSKY_APP_PASSWORD are not set "
              "(add them as repository Secrets).", file=sys.stderr)
        return 1

    session = api(service, "POST", "com.atproto.server.createSession",
                  body={"identifier": handle, "password": app_password})
    token, did = session["accessJwt"], session["did"]
    print(f"logged in as {session.get('handle')} ({did})")

    if not args.force:
        existing = fetch_own_texts(service, token, did)
        if chunks[0].strip() in [t.strip() for t in existing]:
            print(json.dumps({"status": "skipped", "reason": "first chunk already posted",
                              "target_date": post["target_date"],
                              "content_hash": post["content_hash"]}, ensure_ascii=False))
            return 0

    root = parent = None
    posted = []
    for i, chunk in enumerate(chunks):
        record = {
            "$type": "app.bsky.feed.post",
            "text": chunk,
            "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "langs": ["ja"],
        }
        facets = build_facets(chunk)
        if facets:
            record["facets"] = facets
        if parent is not None:
            record["reply"] = {"root": root, "parent": parent}
        out = api(service, "POST", "com.atproto.repo.createRecord", token=token,
                  body={"repo": did, "collection": "app.bsky.feed.post", "record": record})
        ref = {"uri": out["uri"], "cid": out["cid"]}
        if root is None:
            root = ref
        parent = ref
        posted.append(out["uri"])
        print(f"posted chunk {i + 1}: {out['uri']}")

    first = posted[0] if posted else ""
    rkey = first.rsplit("/", 1)[-1] if first else ""
    print(json.dumps({
        "status": "posted",
        "chunks": len(posted),
        "uris": posted,
        "url": f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey else None,
        "target_date": post["target_date"],
        "content_hash": post["content_hash"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
