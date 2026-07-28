import os
import re
import json
import glob
import time
import requests
import subprocess
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
MAX_PER_DAY = 5
WF_DIR = ".github/workflows"
TIMESTAMP_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})\.yml$")
DEFAULT_CF_MODEL = "@cf/google/gemma-4-26b-a4b-it"


def now_jst():
    return datetime.now(JST)


def safe_get_json(url, timeout=15):
    """
    GETしてJSONを返す。失敗時はstatus/bodyの一部をログに出して原因特定しやすくする。
    """
    status = None
    body_preview = ""
    try:
        r = requests.get(url, timeout=timeout)
        status = r.status_code
        body_preview = (r.text or "")[:200]
        r.raise_for_status()
        if not r.text.strip():
            print(f"[WARN] empty body from {url} (status={status})")
            return None
        return r.json()
    except Exception as e:
        print(f"[WARN] GET JSON failed: {url} -> {e} | status={status} body={body_preview!r}")
        return None


def truncate80(s):
    if not s:
        return ""
    return str(s).strip()[:80]


def fetch_today_label():
    data = safe_get_json("https://harutv.stars.ne.jp/today")
    if isinstance(data, dict):
        return truncate80(data.get("today", ""))
    return ""


def fetch_matsudo_weather():
    url = "https://www.jma.go.jp/bosai/forecast/data/forecast/120000.json"
    data = safe_get_json(url)
    temp = ""
    hum = ""

    try:
        dump = json.dumps(data, ensure_ascii=False)
        m_temp = re.search(r'(-?\d+)\s*℃', dump)
        if m_temp:
            temp = m_temp.group(1)
        m_hum = re.search(r'湿度[^0-9]{0,10}(\d{1,3})', dump)
        if m_hum:
            hum = m_hum.group(1)
    except Exception as e:
        print(f"[WARN] parse weather failed: {e}")

    return {"temperature_c": temp, "humidity_pct": hum}


def fetch_task_titles():
    token = os.getenv("TASKS_TOKEN", "")
    if not token:
        print("[WARN] TASKS_TOKEN missing.")
        return []

    url = f"https://harutv.stars.ne.jp/tasks?token={token}&api=1"

    data = None
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        data = safe_get_json(url)
        if isinstance(data, dict):
            break
        # print(f"[WARN] retrying tasks fetch (attempt {attempt}/{max_attempts})")
        if attempt < max_attempts:
            time.sleep(2)

    print(data)
    if not isinstance(data, dict):
        return []

    tasks = data.get("tasks", [])
    if not isinstance(tasks, list):
        return []

    titles = []
    for t in tasks:
        if isinstance(t, dict):
            title = str(t.get("title", "")).strip()
            if title:
                titles.append(title)
    return titles


def list_timestamp_workflows_local():
    files = glob.glob(f"{WF_DIR}/*.yml")
    return sorted([p for p in files if TIMESTAMP_RE.match(os.path.basename(p))])


def parse_count_and_date_from_filename(filename):
    m = TIMESTAMP_RE.match(filename)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def read_count_from_first_line(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            first = f.readline().strip()
        cm = re.match(r"^#\s*(\d+)\s*$", first)
        if cm:
            return int(cm.group(1))
    except Exception as e:
        print(f"[WARN] failed to read first line count from {path}: {e}")
    return 0


def get_remote_timestamp_files_via_api():
    """
    repo上の .github/workflows をGitHub APIで列挙し、
    yyyy-mm-dd-hh-mm-ss.yml のみ返す。
    """
    token = os.getenv("GH_TOKEN", "")
    repo = os.getenv("REPO", "")
    if not token or not repo:
        print("[WARN] GH_TOKEN or REPO missing; cannot list remote workflow files.")
        return []

    url = f"https://api.github.com/repos/{repo}/contents/.github/workflows"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    try:
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        items = r.json()
        out = []
        if isinstance(items, list):
            for it in items:
                name = it.get("name", "")
                if TIMESTAMP_RE.match(name):
                    out.append({
                        "name": name,
                        "path": it.get("path"),
                        "sha": it.get("sha"),
                        "download_url": it.get("download_url")
                    })
        return sorted(out, key=lambda x: x["name"])
    except Exception as e:
        print(f"[WARN] failed to list remote workflow files: {e}")
        return []


def get_remote_today_count(today_str):
    """
    remoteの timestamp workflow から最新1件を見て、
    同日なら先頭 #count を返す。異日なら0。
    """
    files = get_remote_timestamp_files_via_api()
    if not files:
        return 0

    latest = files[-1]
    file_date = parse_count_and_date_from_filename(latest["name"])
    if file_date != today_str:
        return 0

    # 先頭行取得
    dl = latest.get("download_url")
    if not dl:
        return 0
    try:
        txt = requests.get(dl, timeout=20).text
        first = txt.splitlines()[0].strip() if txt else ""
        m = re.match(r"^#\s*(\d+)\s*$", first)
        if m:
            return int(m.group(1))
    except Exception as e:
        print(f"[WARN] failed to read remote temp workflow first line: {e}")
    return 0


def cleanup_timestamp_workflows_local():
    for p in list_timestamp_workflows_local():
        try:
            os.remove(p)
            print(f"[INFO] removed local temp workflow: {p}")
        except Exception as e:
            print(f"[WARN] failed removing local {p}: {e}")


def cleanup_timestamp_workflows_remote():
    """
    remote上の timestamp yml をgit rmし、後でコミット対象にする。
    """
    files = get_remote_timestamp_files_via_api()
    removed = 0
    for f in files:
        p = f["path"]
        try:
            subprocess.run(["git", "rm", "-f", p], check=False)
            removed += 1
            print(f"[INFO] staged remove remote temp workflow: {p}")
        except Exception as e:
            print(f"[WARN] failed git rm {p}: {e}")
    return removed


def extract_text_from_cf_result(data, allow_reasoning_time_fallback=False):
    result = data.get("result")

    if isinstance(result, str) and result.strip():
        return result.strip()

    if isinstance(result, dict):
        for k in ["response", "text", "output", "generated_text"]:
            v = result.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()

        choices = result.get("choices")
        if isinstance(choices, list) and choices:
            c0 = choices[0]
            if isinstance(c0, dict):
                msg = c0.get("message")
                if isinstance(msg, dict):
                    content = msg.get("content")
                    if isinstance(content, str) and content.strip():
                        return content.strip()

                    if allow_reasoning_time_fallback:
                        rc = msg.get("reasoning_content")
                        if isinstance(rc, str):
                            m = re.search(r"\b([01]\d|2[0-3]):([0-5]\d)\b", rc)
                            if m:
                                return f"{m.group(1)}:{m.group(2)}"

                txt = c0.get("text")
                if isinstance(txt, str) and txt.strip():
                    return txt.strip()

    if isinstance(result, list) and result:
        first = result[0]
        if isinstance(first, str) and first.strip():
            return first.strip()
        if isinstance(first, dict):
            for k in ["response", "text", "output", "generated_text"]:
                v = first.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()

    return ""


def call_cf_generate(prompt, system_prompt, allow_reasoning_time_fallback=False, max_tokens=1024, temperature=0.4):
    account = os.getenv("CF_ACCOUNT_ID", "")
    token = os.getenv("CF_API_TOKEN", "")
    model = (os.getenv("CF_MODEL") or DEFAULT_CF_MODEL).strip()

    if not account or not token:
        print("[WARN] CF creds missing.")
        return ""

    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    try:
        r = requests.post(url, headers=headers, json=body, timeout=60)
        r.raise_for_status()
        data = r.json()

        text = ""  # 未定義エラー防止のため初期化
        if "result" in data:
            text = extract_text_from_cf_result(
                data,
                allow_reasoning_time_fallback=allow_reasoning_time_fallback
            )
        else:
            print(f"[WARN] CF response has no 'result' key. keys={list(data.keys())}")

        if text.strip():
            return text.strip()

        print(f"[WARN] CF parsed but no usable text. raw_result={str(data.get('result'))[:300]!r}")
        return ""
    except Exception as e:
        print(f"[WARN] CF generate failed: {e}")
        return ""


def post_discord(text):
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "")
    if not webhook:
        raise RuntimeError("DISCORD_WEBHOOK_URL is missing")
    payload = {"content": text[:2000]}
    r = requests.post(webhook, json=payload, timeout=20)
    r.raise_for_status()
    print("[INFO] posted to Discord")


def generate_greeting(now_dt, weather, today_label):
    task_titles = fetch_task_titles()
    tasks_text = " / ".join(task_titles) if task_titles else "なし"

    prompt = (
        f"現在時刻(JST): {now_dt.strftime('%Y-%m-%d %H:%M')}\n"
        f"松戸市の気温(参考): {weather['temperature_c']}\n"
        f"松戸市の湿度(参考): {weather['humidity_pct']}\n"
        f"今日は何の日: {today_label}\n"
        f"今日のタスク: {tasks_text}\n"
        "120文字以内の自然な日本語挨拶を1行だけ出力。"
    )
    # print(tasks_text)
    system_prompt = (
        "今日は何の日かは、記載があり朝の場合のみ読むこと、勝手に知識から回答しない。"
        "夕方の時間帯だけは想像力を持って、楽しませる文章にすること。"
        "あなたは私の公設秘書です。立場をわきまえること。"
        "内部推論は出力せず、最終回答のみで返すこと。適切な場所で改行すること。"
        "思考過程・注釈・JSONは禁止。"
    )
    text = call_cf_generate(
        prompt,
        system_prompt,
        allow_reasoning_time_fallback=False,
        max_tokens=2100,
        temperature=0.9,
    )
    return text if text else "おはようございます！よい一日を。"


def choose_next_time_with_ai(now_dt, sent_text, count):
    prompt = (
        f"現在JST: {now_dt.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"本日送信回数: {count}\n"
        f"直前送信文(80字): {truncate80(sent_text)}\n"
        "次回送信時刻をJSTで1つ。06:00〜22:00、現在より未来、HH:MMのみ。
    )
    system_prompt = "内部推論を出さず、HH:MMのみ返すこと。"
    out = call_cf_generate(
        prompt,
        system_prompt,
        allow_reasoning_time_fallback=True,
        max_tokens=400,
        temperature=0.2,
    )

    m = re.search(r"\b([01]\d|2[0-3]):([0-5]\d)\b", out or "")
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        hh = min(max(hh, 6), 22)
        candidate = now_dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if candidate <= now_dt:
            candidate += timedelta(days=1)
        return candidate

    fallback = now_dt + timedelta(hours=3)
    if fallback.hour < 6:
        fallback = fallback.replace(hour=6, minute=0, second=0, microsecond=0)
    if fallback.hour > 22:
        fallback = (fallback + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
    return fallback


def cron_utc_for_jst(dt_jst):
    dt_utc = dt_jst.astimezone(timezone.utc)
    return dt_utc.minute, dt_utc.hour, dt_utc.day, dt_utc.month


def write_temp_workflow(next_dt_jst, count):
    minute, hour, day, month = cron_utc_for_jst(next_dt_jst)
    fname = next_dt_jst.strftime("%Y-%m-%d-%H-%M-%S.yml")
    path = os.path.join(WF_DIR, fname)
    workflow_id = os.getenv("WORKFLOW_ID", "main_greet.yml")

    content = f"""#{count}
name: temp-trigger-{next_dt_jst.strftime("%Y%m%d%H%M%S")}
on:
  schedule:
    - cron: "{minute} {hour} {day} {month} *"
  workflow_dispatch:

permissions:
  actions: write
  contents: write

jobs:
  trigger:
    runs-on: ubuntu-latest
    steps:
      - name: Trigger main workflow
        env:
          GH_TOKEN: ${{{{ secrets.GH_TOKEN }}}}
          REPO: ${{{{ github.repository }}}}
          WORKFLOW_ID: {workflow_id}
        run: |
          curl -sS -X POST \\
            -H "Authorization: Bearer $GH_TOKEN" \\
            -H "Accept: application/vnd.github+json" \\
            https://api.github.com/repos/$REPO/actions/workflows/$WORKFLOW_ID/dispatches \\
            -d '{{"ref":"main"}}'
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[INFO] wrote local temp workflow: {path}")
    return path


def git_commit_and_push(message):
    token = os.getenv("GH_TOKEN", "")
    repo = os.getenv("REPO", "")
    if not token or not repo:
        print("[WARN] GH_TOKEN or REPO missing; skip push.")
        return

    actor = os.getenv("GITHUB_ACTOR", "github-actions[bot]")

    try:
        subprocess.run(["git", "config", "user.name", actor], check=True)
        subprocess.run(["git", "config", "user.email", f"{actor}@users.noreply.github.com"], check=True)

        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, check=True
        )
        if not status.stdout.strip():
            print("[INFO] no git changes to commit.")
            return

        subprocess.run(["git", "add", "-A"], check=True)
        subprocess.run(["git", "commit", "-m", message], check=True)

        # origin に依存せず、PAT付きURLへ直接 push
        push_url = f"https://x-access-token:{token}@github.com/{repo}.git"
        print(f"[DEBUG] pushing to https://x-access-token:***@github.com/{repo}.git")
        subprocess.run(["git", "push", push_url, "HEAD:main"], check=True)

        print("[INFO] pushed workflow changes to remote.")
    except subprocess.CalledProcessError as e:
        print(f"[WARN] git push failed (non-fatal): {e}")
        # 運用優先: 送信自体は成功しているためジョブは落とさない
        return


def main():
    os.makedirs(WF_DIR, exist_ok=True)

    now = now_jst()
    today_str = now.strftime("%Y-%m-%d")

    # 回数は remote を基準に判定（手動実行でもぶれにくい）
    prev_count = get_remote_today_count(today_str)
    next_count = prev_count + 1
    print(f"[INFO] today count(remote): {prev_count} -> next: {next_count}")

    # 0) local / remote の timestamp yml を掃除
    cleanup_timestamp_workflows_local()
    removed_remote = cleanup_timestamp_workflows_remote()
    print(f"[INFO] staged remote removals: {removed_remote}")

    if next_count > MAX_PER_DAY:
        print(f"[INFO] daily cap reached ({prev_count}/5). skip send/schedule.")
        git_commit_and_push(f"chore: cleanup temp workflows ({today_str})")
        return

    weather = fetch_matsudo_weather()
    today_label = fetch_today_label()

    # 1) Discord送信
    msg = generate_greeting(now, weather, today_label)
    post_discord(msg)

    # 2) 次回時刻＆temp yml作成
    next_dt = choose_next_time_with_ai(now, msg, next_count)
    write_temp_workflow(next_dt, next_count)

    # 3) commit/push して実際に repo に残す
    git_commit_and_push(f"chore: schedule next greet #{next_count} ({next_dt.strftime('%Y-%m-%d %H:%M:%S JST')})")


if __name__ == "__main__":
    main()
