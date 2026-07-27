import os
import re
import json
import glob
import requests
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
MAX_PER_DAY = 5
WF_DIR = ".github/workflows"
TIMESTAMP_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})\.yml$")
DEFAULT_CF_MODEL = "@cf/google/gemma-4-26b-a4b-it"

def now_jst():
    return datetime.now(JST)

def safe_get_json(url, timeout=15):
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[WARN] GET JSON failed: {url} -> {e}")
        return None

def truncate80(s):
    if not s:
        return ""
    s = str(s).strip()
    return s[:80]

def fetch_today_label():
    data = safe_get_json("https://harutv.stars.ne.jp/today")
    if isinstance(data, dict):
        return truncate80(data.get("today", ""))
    return ""

def fetch_matsudo_weather():
    """
    気象庁公開JSONから松戸市相当(千葉エリア)の情報を可能な範囲で取得。
    厳密な観測点マッピングは将来拡張し、ここでは失敗時フォールバック優先。
    """
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

def list_timestamp_workflows():
    files = glob.glob(f"{WF_DIR}/*.yml")
    out = []
    for p in files:
        if TIMESTAMP_RE.match(os.path.basename(p)):
            out.append(p)
    return sorted(out)

def parse_count_and_date_from_file(path):
    count = 0
    fname = os.path.basename(path)
    m = TIMESTAMP_RE.match(fname)
    file_date = None
    if m:
        file_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    try:
        with open(path, "r", encoding="utf-8") as f:
            first = f.readline().strip()
            cm = re.match(r"^#\s*(\d+)\s*$", first)
            if cm:
                count = int(cm.group(1))
    except Exception as e:
        print(f"[WARN] read count failed: {path} -> {e}")

    return count, file_date

def read_latest_count_for_today(today_str):
    files = list_timestamp_workflows()
    if not files:
        return 0
    latest = files[-1]
    count, file_date = parse_count_and_date_from_file(latest)
    if file_date == today_str:
        return count
    return 0

def cleanup_timestamp_workflows():
    for p in list_timestamp_workflows():
        try:
            os.remove(p)
            print(f"[INFO] removed old temp workflow: {p}")
        except Exception as e:
            print(f"[WARN] failed removing {p}: {e}")

def call_cf_generate(prompt, system_prompt="あなたは丁寧で簡潔な日本語アシスタントです。"):
    account = os.getenv("CF_ACCOUNT_ID", "")
    token = os.getenv("CF_API_TOKEN", "")
    model = os.getenv("CF_MODEL", DEFAULT_CF_MODEL)

    if not account or not token:
        print("[WARN] CF creds missing; fallback text.")
        return "こんにちは！今日も無理なくいきましょう。"

    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
    }

    try:
        r = requests.post(url, headers=headers, json=body, timeout=40)
        r.raise_for_status()
        data = r.json()
        result = data.get("result", {})
        if isinstance(result, dict):
            if isinstance(result.get("response"), str):
                return result["response"].strip()
            if isinstance(result.get("text"), str):
                return result["text"].strip()
        return "おはようございます！よい一日を。"
    except Exception as e:
        print(f"[WARN] CF generate failed: {e}")
        return "おはようございます！よい一日を。"

def post_discord(text):
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "")
    if not webhook:
        raise RuntimeError("DISCORD_WEBHOOK_URL is missing")
    payload = {"content": text[:2000]}  # Discord message limit
    r = requests.post(webhook, json=payload, timeout=20)
    r.raise_for_status()
    print("[INFO] posted to Discord")

def choose_next_time_with_ai(now_dt, sent_text, count):
    prompt = (
        f"現在JST: {now_dt.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"本日送信回数: {count}\n"
        f"直前送信文(80字): {truncate80(sent_text)}\n"
        "次回送信時刻をJSTで1つ決めてください。条件:\n"
        "- 06:00〜22:00\n"
        "- 現在時刻より未来\n"
        "- 出力は HH:MM のみ"
    )
    out = call_cf_generate(prompt, system_prompt="あなたは時刻計画アシスタントです。指定形式だけを返してください。")
    m = re.search(r"\b([01]\d|2[0-3]):([0-5]\d)\b", out)

    if m:
        hh = int(m.group(1))
        mm = int(m.group(2))
        hh = min(max(hh, 6), 22)
        candidate = now_dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if candidate <= now_dt:
            candidate += timedelta(days=1)
        if candidate.hour < 6:
            candidate = candidate.replace(hour=6, minute=0)
        if candidate.hour > 22:
            candidate = candidate.replace(hour=22, minute=0)
        return candidate

    # fallback
    candidate = now_dt + timedelta(hours=3)
    if candidate.hour < 6:
        candidate = candidate.replace(hour=6, minute=0, second=0, microsecond=0)
    elif candidate.hour > 22:
        candidate = (candidate + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
    return candidate

def cron_utc_for_jst(dt_jst):
    dt_utc = dt_jst.astimezone(timezone.utc)
    return dt_utc.minute, dt_utc.hour, dt_utc.day, dt_utc.month

def write_temp_workflow(next_dt_jst, count):
    minute, hour, day, month = cron_utc_for_jst(next_dt_jst)
    fname = next_dt_jst.strftime("%Y-%m-%d-%H-%M-%S.yml")
    path = os.path.join(WF_DIR, fname)
    workflow_id = os.getenv("WORKFLOW_ID", "main-greet.yml")

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
    print(f"[INFO] wrote next temp workflow: {path}")

def main():
    os.makedirs(WF_DIR, exist_ok=True)

    now = now_jst()
    today_str = now.strftime("%Y-%m-%d")

    prev_count = read_latest_count_for_today(today_str)
    next_count = prev_count + 1

    # 0. 既存 yyyy-mm-dd-hh-mm-ss.yml を消す
    cleanup_timestamp_workflows()

    # 1日5回制限
    if next_count > MAX_PER_DAY:
        print(f"[INFO] daily cap reached ({prev_count}/5). skip send.")
        return

    weather = fetch_matsudo_weather()
    today_label = fetch_today_label()

    prompt = (
        f"現在時刻(JST): {now.strftime('%Y-%m-%d %H:%M')}\n"
        f"松戸市の気温(参考): {weather['temperature_c']}\n"
        f"松戸市の湿度(参考): {weather['humidity_pct']}\n"
        f"今日は何の日: {today_label}\n"
        "これらを自然に織り込み、120文字以内の日本語の挨拶文を1つ作ってください。"
    )
    message = call_cf_generate(prompt)
    message = message[:2000]

    # 1. 挨拶をDiscordへ送信
    post_discord(message)

    # 2. 次時刻をAIで決めて仮設workflow作成
    next_dt = choose_next_time_with_ai(now, message, next_count)

    # 同日5回目に達したら同日の追加予約はしない
    if next_count >= MAX_PER_DAY and next_dt.strftime("%Y-%m-%d") == today_str:
        print("[INFO] reached 5th send today. no further schedule today.")
        return

    write_temp_workflow(next_dt, next_count)

if __name__ == "__main__":
    main()
