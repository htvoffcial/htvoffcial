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
    JMA time series data (Chiba area) with fallback.
    We attempt to read temperature/humidity fields if present.
    """
    # Chiba forecast endpoint (official JMA JSON format)
    url = "https://www.jma.go.jp/bosai/forecast/data/forecast/120000.json"
    data = safe_get_json(url)
    temp = None
    hum = None

    try:
        if isinstance(data, list) and len(data) > 0:
            # structure can vary; attempt robust extraction
            ts = data[0].get("timeSeries", [])
            # humidity often in area weather text tables; temperature often in second day section
            dump = json.dumps(ts, ensure_ascii=False)
            # very rough fallback parse
            m_temp = re.search(r'(-?\d+)\s*℃', dump)
            if m_temp:
                temp = m_temp.group(1)
            m_hum = re.search(r'湿度[^0-9]{0,10}(\d{1,3})', dump)
            if m_hum:
                hum = m_hum.group(1)
    except Exception as e:
        print(f"[WARN] parse weather failed: {e}")

    return {
        "temperature_c": temp if temp is not None else "",
        "humidity_pct": hum if hum is not None else ""
    }

def list_timestamp_workflows():
    files = glob.glob(f"{WF_DIR}/*.yml")
    out = []
    for p in files:
        name = os.path.basename(p)
        if TIMESTAMP_RE.match(name):
            out.append(p)
    return sorted(out)

def parse_count_and_date_from_file(path):
    """
    first line: #1
    filename: yyyy-mm-dd-hh-mm-ss.yml
    """
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
        print(f"[WARN] reading {path}: {e}")

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

def call_cf_generate(prompt):
    account = os.getenv("CF_ACCOUNT_ID", "")
    token = os.getenv("CF_API_TOKEN", "")
    model = os.getenv("CF_MODEL", DEFAULT_CF_MODEL)
    if not account or not token:
        print("[WARN] Cloudflare credentials missing; return fallback text")
        return "おはようございます！今日もよい一日をお過ごしください。"

    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = {
        "messages": [
            {"role": "system", "content": "あなたは丁寧で簡潔な日本語アシスタントです。120文字以内で挨拶文を作成してください。"},
            {"role": "user", "content": prompt}
        ]
    }
    try:
        r = requests.post(url, headers=headers, json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        # generic extraction
        result = data.get("result", {})
        if isinstance(result, dict):
            if "response" in result and isinstance(result["response"], str):
                return result["response"].strip()
            if "text" in result and isinstance(result["text"], str):
                return result["text"].strip()
        return "こんにちは！今日も無理なくいきましょう。"
    except Exception as e:
        print(f"[WARN] CF generate failed: {e}")
        return "こんにちは！今日も無理なくいきましょう。"

def post_google_chat(text):
    webhook = os.getenv("GOOGLE_CHAT_WEBHOOK_URL", "")
    if not webhook:
        raise RuntimeError("GOOGLE_CHAT_WEBHOOK_URL is missing")
    payload = {"text": text}
    r = requests.post(webhook, json=payload, timeout=20)
    r.raise_for_status()
    print("[INFO] posted to Google Chat")

def choose_next_time_with_ai(now_dt, sent_text, count):
    base_prompt = (
        f"現在JST: {now_dt.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"本日送信回数: {count}\n"
        f"直前送信文(80字): {truncate80(sent_text)}\n"
        "次回送信時刻をJSTで1つ決めてください。条件: 今日または明日、06:00〜22:00。"
        "出力は HH:MM のみ。"
    )
    out = call_cf_generate(base_prompt)
    m = re.search(r"\b([01]\d|2[0-3]):([0-5]\d)\b", out)
    if m:
        hh = int(m.group(1)); mm = int(m.group(2))
        hh = min(max(hh, 6), 22)
        candidate = now_dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if candidate <= now_dt:
            candidate = candidate + timedelta(days=1)
        # bound next day too
        if candidate.hour < 6:
            candidate = candidate.replace(hour=6, minute=0)
        if candidate.hour > 22:
            candidate = candidate.replace(hour=22, minute=0)
        return candidate

    # fallback: +3h in range
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

    # まず既存仮設workflowを掃除
    cleanup_timestamp_workflows()

    if next_count > MAX_PER_DAY:
        print(f"[INFO] daily cap reached: {prev_count} -> skip send")
        return

    w = fetch_matsudo_weather()
    today_label = fetch_today_label()

    prompt = (
        f"現在時刻(JST): {now.strftime('%Y-%m-%d %H:%M')}\n"
        f"松戸市の気温(参考): {w['temperature_c']}\n"
        f"松戸市の湿度(参考): {w['humidity_pct']}\n"
        f"今日は何の日: {today_label}\n"
        "上を参考に、自然で短い日本語の挨拶を1つ作成してください。"
    )
    message = call_cf_generate(prompt)
    if len(message) > 200:
        message = message[:200]

    post_google_chat(message)

    next_dt = choose_next_time_with_ai(now, message, next_count)

    # 同日5回目ならこれ以上スケジュールしない
    if next_count >= MAX_PER_DAY and next_dt.strftime("%Y-%m-%d") == today_str:
        print("[INFO] reached 5th send today; no more schedule for today")
        return

    write_temp_workflow(next_dt, next_count)

if __name__ == "__main__":
    main()
