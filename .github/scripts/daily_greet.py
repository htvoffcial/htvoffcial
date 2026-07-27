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
    return str(s).strip()[:80]


def fetch_today_label():
    data = safe_get_json("https://harutv.stars.ne.jp/today")
    if isinstance(data, dict):
        return truncate80(data.get("today", ""))
    return ""


def fetch_matsudo_weather():
    """
    気象庁公開JSONから松戸市相当(千葉エリア)の情報を可能な範囲で取得。
    失敗しても空値で継続する。
    """
    url = "https://www.jma.go.jp/bosai/forecast/data/forecast/120000.json"
    data = safe_get_json(url)
    temp = ""
    hum = ""

    try:
        dump = json.dumps(data, ensure_ascii=False)
        # ざっくり抽出（将来必要なら地点コードで厳密化）
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
    return sorted([p for p in files if TIMESTAMP_RE.match(os.path.basename(p))])


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


def extract_text_from_cf_result(data, allow_reasoning_time_fallback=False):
    """
    Cloudflare Workers AI の揺れるレスポンス形式からテキスト抽出。
    allow_reasoning_time_fallback=True の場合のみ、reasoning_content から HH:MM を救済。
    """
    result = data.get("result")

    # 1) result が文字列
    if isinstance(result, str) and result.strip():
        return result.strip()

    # 2) result が dict
    if isinstance(result, dict):
        # 直接キー候補
        for k in ["response", "text", "output", "generated_text"]:
            v = result.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()

        # OpenAI互換 choices
        choices = result.get("choices")
        if isinstance(choices, list) and choices:
            c0 = choices[0]
            if isinstance(c0, dict):
                msg = c0.get("message")
                if isinstance(msg, dict):
                    content = msg.get("content")
                    if isinstance(content, str) and content.strip():
                        return content.strip()

                    # 時刻決定時のみ rescue
                    if allow_reasoning_time_fallback:
                        rc = msg.get("reasoning_content")
                        if isinstance(rc, str):
                            m = re.search(r"\b([01]\d|2[0-3]):([0-5]\d)\b", rc)
                            if m:
                                return f"{m.group(1)}:{m.group(2)}"

                txt = c0.get("text")
                if isinstance(txt, str) and txt.strip():
                    return txt.strip()

    # 3) result が list
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


def call_cf_generate(prompt, system_prompt, allow_reasoning_time_fallback=False):
    account = os.getenv("CF_ACCOUNT_ID", "")
    token = os.getenv("CF_API_TOKEN", "")
    model = (os.getenv("CF_MODEL") or DEFAULT_CF_MODEL).strip()

    if not account or not token:
        print("[WARN] CF creds missing; fallback text.")
        return ""

    if not model:
        model = DEFAULT_CF_MODEL

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
        "max_tokens": 512,
        "temperature": 0.7,
    }

    try:
        r = requests.post(url, headers=headers, json=body, timeout=45)
        r.raise_for_status()
        data = r.json()

        # debug
        print("[DEBUG] CF response keys:", list(data.keys()))
        if "result" in data:
            print("[DEBUG] CF result type:", type(data["result"]).__name__)
            print("[DEBUG] CF result preview:", str(data["result"])[:500])

        text = extract_text_from_cf_result(
            data, allow_reasoning_time_fallback=allow_reasoning_time_fallback
        )
        if text.strip():
            return text.strip()

        print("[WARN] CF response parsed but no usable text.")
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
    prompt = (
        f"現在時刻(JST): {now_dt.strftime('%Y-%m-%d %H:%M')}\n"
        f"松戸市の気温(参考): {weather['temperature_c']}\n"
        f"松戸市の湿度(参考): {weather['humidity_pct']}\n"
        f"今日は何の日: {today_label}\n"
        "条件:\n"
        "- 120文字以内\n"
        "- 自然な日本語の挨拶\n"
        "- 出力は挨拶文そのもの1行のみ\n"
        "- 思考過程・注釈・JSONは禁止"
    )
    system_prompt = (
        "あなたは日本語アシスタントです。"
        "内部推論は出力せず、最終回答のみを返してください。"
        "説明や前置きは禁止。"
    )

    text = call_cf_generate(prompt, system_prompt, allow_reasoning_time_fallback=False)
    if not text:
        return "おはようございます！よい一日を。"
    return text[:2000]


def choose_next_time_with_ai(now_dt, sent_text, count):
    prompt = (
        f"現在JST: {now_dt.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"本日送信回数: {count}\n"
        f"直前送信文(80字): {truncate80(sent_text)}\n"
        "次回送信時刻をJSTで1つ決めてください。\n"
        "条件:\n"
        "- 06:00〜22:00\n"
        "- 現在時刻より未来\n"
        "- 出力は HH:MM のみ\n"
        "- 余計な説明は禁止"
    )
    system_prompt = (
        "あなたは時刻計画アシスタントです。"
        "内部推論は出力せず、最終回答のみを返してください。"
        "必ず HH:MM 形式のみを返してください。"
    )

    out = call_cf_generate(prompt, system_prompt, allow_reasoning_time_fallback=True)
    m = re.search(r"\b([01]\d|2[0-3]):([0-5]\d)\b", out or "")

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
        candidate = (candidate + timedelta(days=1)).replace(
            hour=6, minute=0, second=0, microsecond=0
        )
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
          curl -sS -X POST \
            -H "Authorization: Bearer $GH_TOKEN" \
            -H "Accept: application/vnd.github+json" \
            https://api.github.com/repos/$REPO/actions/workflows/$WORKFLOW_ID/dispatches \
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

    # 0. 既存の timestamp workflow を掃除
    cleanup_timestamp_workflows()

    # 1日5回上限
    if next_count > MAX_PER_DAY:
        print(f"[INFO] daily cap reached ({prev_count}/5). skip send.")
        return

    weather = fetch_matsudo_weather()
    today_label = fetch_today_label()

    # 1. 挨拶生成＆送信
    message = generate_greeting(now, weather, today_label)
    post_discord(message)

    # 2. 次回時刻決定＆仮設workflow作成
    next_dt = choose_next_time_with_ai(now, message, next_count)

    # 同日5回目ならそれ以上同日予約しない
    if next_count >= MAX_PER_DAY and next_dt.strftime("%Y-%m-%d") == today_str:
        print("[INFO] reached 5th send today. no further schedule today.")
        return

    write_temp_workflow(next_dt, next_count)


if __name__ == "__main__":
    main()
