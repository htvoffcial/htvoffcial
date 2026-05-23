/*
 Copyright (C) 2026 htvoffcial
 SPDX-License-Identifier: SSPL-1.0

 This program is free software: you can use, redistribute,
 and/or modify it under the terms of the Server Side
 Public License, version 1, as published by MongoDB, Inc.
*/
import fs from "node:fs";

const GH_TOKEN = process.env.GH_TOKEN;
const CF_ACCOUNT_ID = process.env.CF_ACCOUNT_ID;
const CF_API_TOKEN = process.env.CF_API_TOKEN;

if (!GH_TOKEN) throw new Error("GH_TOKEN is missing");
if (!CF_ACCOUNT_ID) throw new Error("CF_ACCOUNT_ID is missing");
if (!CF_API_TOKEN) throw new Error("CF_API_TOKEN is missing");

const OWNER = process.env.GITHUB_REPOSITORY_OWNER;
const REPO = (process.env.GITHUB_REPOSITORY || "").split("/")[1];

if (!OWNER || !REPO) throw new Error("Missing GITHUB_REPOSITORY(_OWNER) env");

const CF_MODEL = "@cf/google/gemma-4-26b-a4b-it";

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// WMO 天気コード デコーダー (Open-Meteo用)
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
const WMO_MAP = {
  0:  { label: "快晴",             icon: "☀️",  category: "sunny"  },
  1:  { label: "ほぼ晴れ",         icon: "🌤️", category: "sunny"  },
  2:  { label: "晴れ時々曇り",     icon: "⛅",  category: "cloudy" },
  3:  { label: "曇り",             icon: "☁️",  category: "cloudy" },
  45: { label: "霧",               icon: "🌫️", category: "fog"    },
  48: { label: "霧（着氷性）",     icon: "🌫️", category: "fog"    },
  51: { label: "霧雨（弱）",       icon: "🌦️", category: "rain"   },
  53: { label: "霧雨（中）",       icon: "🌦️", category: "rain"   },
  55: { label: "霧雨（強）",       icon: "🌧️", category: "rain"   },
  56: { label: "着氷性霧雨（弱）", icon: "🌧️", category: "rain"   },
  57: { label: "着氷性霧雨（強）", icon: "🌧️", category: "rain"   },
  61: { label: "雨（弱）",         icon: "🌧️", category: "rain"   },
  63: { label: "雨（中）",         icon: "🌧️", category: "rain"   },
  65: { label: "雨（強）",         icon: "🌧️", category: "rain"   },
  66: { label: "着氷性の雨（弱）", icon: "🌨️", category: "sleet"  },
  67: { label: "着氷性の雨（強）", icon: "🌨️", category: "sleet"  },
  71: { label: "雪（弱）",         icon: "🌨️", category: "snow"   },
  73: { label: "雪（中）",         icon: "❄️",  category: "snow"   },
  75: { label: "雪（強）",         icon: "❄️",  category: "snow"   },
  77: { label: "霰",               icon: "🌨️", category: "snow"   },
  80: { label: "にわか雨（弱）",   icon: "🌦️", category: "rain"   },
  81: { label: "にわか雨（中）",   icon: "🌧️", category: "rain"   },
  82: { label: "にわか雨（強）",   icon: "⛈️",  category: "rain"   },
  85: { label: "にわか雪（弱）",   icon: "🌨️", category: "snow"   },
  86: { label: "にわか雪（強）",   icon: "❄️",  category: "snow"   },
  95: { label: "雷雨",             icon: "⛈️",  category: "storm"  },
  96: { label: "雷雨＋雹（小）",   icon: "⛈️",  category: "storm"  },
  99: { label: "雷雨＋雹（大）",   icon: "⛈️",  category: "storm"  },
};

/**
 * WMOコードをデコードして天気情報を返す
 * @param {number} code - weathercode の値
 * @returns {{ code, label, icon, category, isSunny, isRainy, isCloudy, isSnowy, isStormy }}
 */
function decodeWeather(code) {
  const entry = WMO_MAP[code] ?? { label: "不明", icon: "❓", category: "unknown" };
  return {
    code,
    label:    entry.label,
    icon:     entry.icon,
    category: entry.category,
    isSunny:  entry.category === "sunny",
    isRainy:  entry.category === "rain",
    isCloudy: entry.category === "cloudy",
    isSnowy:  entry.category === "snow",
    isStormy: entry.category === "storm",
  };
}

/**
 * 時間帯ごとの weathercode 配列を一括デコード
 * @param {number[]} codes
 * @returns {ReturnType<typeof decodeWeather>[]}
 */
function decodeWeatherAll(codes) {
  return codes.map(decodeWeather);
}

/**
 * デコード済み天気配列から代表天気（最頻カテゴリ）を返す
 * @param {ReturnType<typeof decodeWeather>[]} weatherList
 * @returns {ReturnType<typeof decodeWeather>}
 */
function getDominantWeather(weatherList) {
  const freq = {};
  for (const w of weatherList) freq[w.category] = (freq[w.category] ?? 0) + 1;
  const dominantCategory = Object.entries(freq).sort((a, b) => b[1] - a[1])[0][0];
  return weatherList.find(w => w.category === dominantCategory);
}
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 気象庁アメダス（bosai）簡易ユーティリティ
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * 気象庁アメダス bosai の point データは 3時間ごとのファイルに分割されている。
 * 例) YYYYMMDD_00,03,06,...,21
 * @param {string} yyyymmdd
 * @returns {string[]}
 */
function amedasH3List(yyyymmdd) {
  return ["00", "03", "06", "09", "12", "15", "18", "21"].map((hh) => `${yyyymmdd}_${hh}`);
}

/**
 * @param {string} s
 * @returns {string}
 */
function pad2(s) {
  return String(s).padStart(2, "0");
}

/**
 * JST日付(YYYY-MM-DD) → 気象庁アメダス用(YYYYMMDD)
 * @param {string} dayJst
 */
function toYyyymmdd(dayJst) {
  return dayJst.replaceAll("-", "");
}

/**
 * ざっくり「代表天気」を作る。
 * アメダスは WMO weathercode を返さないので、
 * 日中(6-18時)の1時間降水量の有無で晴/雨を判定し、
 * 欠測が多い場合は unknown。
 *
 * @param {{hour:number, precipitation1hMm:number|null}[]} hourly
 * @returns {{code:number, label:string, icon:string, category:string, isSunny:boolean, isRainy:boolean, isCloudy:boolean, isSnowy:boolean, isStormy:boolean}}
 */
function dominantWeatherFromAmedas(hourly) {
  const daytime = hourly.filter((x) => x.hour >= 6 && x.hour <= 18);

  const valid = daytime.filter((x) => typeof x.precipitation1hMm === "number");
  const rainy = valid.filter((x) => (x.precipitation1hMm ?? 0) > 0);

  // 欠測だらけの場合
  if (valid.length < 6) {
    return {
      code: -1,
      label: "不明",
      icon: "❓",
      category: "unknown",
      isSunny: false,
      isRainy: false,
      isCloudy: false,
      isSnowy: false,
      isStormy: false,
    };
  }

  if (rainy.length >= 1) {
    return {
      code: 61,
      label: "雨（アメダス判定）",
      icon: "🌧️",
      category: "rain",
      isSunny: false,
      isRainy: true,
      isCloudy: false,
      isSnowy: false,
      isStormy: false,
    };
  }

  return {
    code: 0,
    label: "晴（アメダス判定）",
    icon: "☀️",
    category: "sunny",
    isSunny: true,
    isRainy: false,
    isCloudy: false,
    isSnowy: false,
    isStormy: false,
  };
}

/**
 * AQC付きの値配列から数値を取り出す。
 * bosai/amedas の多くの要素は [value, aqc] 形式。
 * @param {unknown} v
 * @returns {number|null}
 */
function readAmedasNumber(v) {
  if (!Array.isArray(v)) return null;
  const value = v[0];
  const aqc = v[1];

  if (value == null) return null;
  if (typeof value !== "number") return null;

  // AQC: 0 が正常という説明が広く使われているため、それ以外は欠測扱い。
  // （必要なら後で閾値や扱いを拡張）
  if (typeof aqc === "number" && aqc !== 0) return null;

  return value;
}

/**
 * 気象庁 bosai アメダス から、指定日の時系列(10分刻み)を取得し、1時間降水量ベースの簡易サマリーを返す。
 *
 * @param {{amedasPoint:string, dayJst:string}} params
 * @returns {Promise<{dominantWeather: ReturnType<typeof dominantWeatherFromAmedas>, rainyHoursCount:number}>}
 */
async function getAmedasDominantWeatherForDay({ amedasPoint, dayJst }) {
  const yyyymmdd = toYyyymmdd(dayJst);

  const base = "https://www.jma.go.jp/bosai/amedas/data/point";

  // 3時間ごとのJSONを全部取ってマージする
  const urls = amedasH3List(yyyymmdd).map((h3) => `${base}/${amedasPoint}/${h3}.json`);

  /** @type {Record<string, any>} */
  const merged = {};

  for (const url of urls) {
    const res = await fetch(url);

    // 過去データが保持期間外などで404になることがあるので、ここは「失敗しても進む」
    if (!res.ok) {
      console.warn(`AMeDAS fetch skipped: ${res.status} ${url}`);
      continue;
    }

    const json = await res.json().catch(() => null);
    if (!json || typeof json !== "object") continue;

    // 各ファイルは { "2026-05-22T00:00:00+09:00": { ... }, ... } のように時刻キーでぶら下がる
    for (const [t, v] of Object.entries(json)) merged[t] = v;
  }

  // 10分刻みの precipitation1h を1時間単位に集約する
  // （同一hour内で「最後に出てくる値」を代表として採用）
  /** @type {Map<number, number|null>} */
  const precipByHour = new Map();

  for (const t of Object.keys(merged).sort()) {
    const dt = new Date(t);
    // dt は ISO (with +09:00) なので getHours() はローカル環境依存になり得る。
    // ただし Actions の TZ がUTCでも Dateはタイムゾーンオフセットを解釈してUTCに変換するため、
    // JSTのhourを得るにはオフセット込み文字列からのローカル時刻がズレる可能性がある。
    // ここでは文字列から "T..:" 部分を読む。
    const m = t.match(/T(\d{2}):/);
    const hourJst = m ? Number(m[1]) : dt.getUTCHours();

    const p1h = readAmedasNumber(merged[t]?.precipitation1h);
    precipByHour.set(hourJst, p1h);
  }

  const hourly = [];
  for (let h = 0; h < 24; h++) {
    hourly.push({ hour: h, precipitation1hMm: precipByHour.has(h) ? precipByHour.get(h) : null });
  }

  const dominantWeather = dominantWeatherFromAmedas(hourly);

  const rainyHoursCount = hourly.filter((x) => x.hour >= 6 && x.hour <= 18).filter((x) => (x.precipitation1hMm ?? 0) > 0).length;

  return { dominantWeather, rainyHoursCount };
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

// JSTの「昨日」範囲をUTCに変換
function getYesterdayJstRangeUtc() {
  const now = new Date();
  const nowJst = new Date(now.getTime() + 9 * 60 * 60 * 1000);
  const y = new Date(Date.UTC(nowJst.getUTCFullYear(), nowJst.getUTCMonth(), nowJst.getUTCDate() - 1));
  const yyyy = y.getUTCFullYear();
  const mm = String(y.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(y.getUTCDate()).padStart(2, "0");
  const dayJst = `${yyyy}-${mm}-${dd}`;

  const startUtc = new Date(`${dayJst}T00:00:00+09:00`).toISOString().replace(/\.\d{3}Z$/, "Z");
  const endUtc   = new Date(`${dayJst}T23:59:59+09:00`).toISOString().replace(/\.\d{3}Z$/, "Z");

  return { dayJst, startUtc, endUtc };
}

async function ghGraphql(query, variables) {
  const res = await fetch("https://api.github.com/graphql", {
    method: "POST",
    headers: {
      Authorization: `bearer ${GH_TOKEN}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ query, variables }),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`GitHub GraphQL error: ${res.status} ${text}`);
  }
  return res.json();
}

function clampText(s, maxChars) {
  if (!s) return "";
  const t = s.replace(/\r\n/g, "\n").trim();
  if (t.length <= maxChars) return t;
  return t.slice(0, maxChars) + "…(省略)";
}

// トークン（コスト）節約：本文をカットして合計も制限
function buildPromptSource(discussions, { maxPerBodyChars = 800, maxTotalChars = 3500 } = {}) {
  let total = 0;
  const lines = [];

  for (const d of discussions) {
    const body = clampText(d.bodyText || "", maxPerBodyChars);
    const chunk =
`【${d.title}】
URL: ${d.url}
本文(抜粋):
${body}
`;
    if (total + chunk.length > maxTotalChars) break;
    lines.push(chunk);
    total += chunk.length;
  }

  return { source: lines.join("\n"), usedChars: total };
}

async function cfAiChat({ accountId, apiToken, model, messages }) {
  const url = `https://api.cloudflare.com/client/v4/accounts/${accountId}/ai/run/${model}`;

  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ messages }),
  });

  const json = await res.json().catch(() => null);

  // Cloudflareは success=false の場合もあるので両方見る
  if (!res.ok || json?.success === false) {
    const err = JSON.stringify(json ?? { status: res.status }, null, 2);
    throw new Error(`Cloudflare AI error: ${res.status} ${err}`);
  }

  // 代表的な取り方（モデルで形が揺れるので複数候補）
  // - 旧: result.response / result.output_text / result.text
  // - 新: OpenAI互換っぽい result.choices[0].message.content
  const text =
    json?.result?.response ??
    json?.result?.output_text ??
    json?.result?.text ??
    json?.result?.choices?.[0]?.message?.content ??
    json?.result?.choices?.[0]?.text ??
    json?.choices?.[0]?.message?.content ??
    json?.choices?.[0]?.text ??
    null;

  if (!text) {
    throw new Error(`Cloudflare AI: unknown response shape: ${JSON.stringify(json).slice(0, 800)}`);
  }
  return String(text).trim();
}

function replaceBlock(readme, newBlock) {
  const start = "<!-- DISCUSS_COACH_START -->";
  const end   = "<!-- DISCUSS_COACH_END -->";
  const s = readme.indexOf(start);
  const e = readme.indexOf(end);
  if (s === -1 || e === -1 || e < s) {
    throw new Error("README.md does not contain DISCUSS_COACH_START/END markers");
  }
  const before = readme.slice(0, s + start.length);
  const after  = readme.slice(e);
  return `${before}\n${newBlock}\n${after}`;
}

function buildReadmeBlock({ dayJst, text, dominantWeather }) {
  const header = `## Discussまとめ（体操のお兄さん）
**対象日（JST）:** ${dayJst}
`;
  return `${header}\n${text}\n`;
}

async function main() {
  const { dayJst, startUtc, endUtc } = getYesterdayJstRangeUtc();

  // ── 天気（アメダス） ─────────────────────────
  // NOTE: 松戸市に近いアメダス地点コードを固定で指定。
  // もし地点を変えたい場合は、気象庁アメダス画面の amdno=xxxxx を参照。
  // https://www.jma.go.jp/bosai/amedas/
  const AMEDAS_POINT = process.env.AMEDAS_POINT || "44132";

  const { dominantWeather, rainyHoursCount } = await getAmedasDominantWeatherForDay({
    amedasPoint: AMEDAS_POINT,
    dayJst,
  });

  console.log(`天気サマリー(AMeDAS): ${dominantWeather.icon} ${dominantWeather.label} (雨: ${rainyHoursCount}h)`);

  const query = `
query($owner:String!, $repo:String!, $after:String) {
  repository(owner:$owner, name:$repo) {
    discussions(first:50, after:$after, orderBy:{field:CREATED_AT, direction:DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes { title url createdAt bodyText }
    }
  }
}
`;

  const nodes = [];
  let after = null;

  while (true) {
    const data = await ghGraphql(query, { owner: OWNER, repo: REPO, after });
    const page = data?.data?.repository?.discussions;
    const list = page?.nodes || [];

    for (const d of list) {
      if (d.createdAt >= startUtc && d.createdAt <= endUtc) nodes.push(d);
    }

    const oldest = list.length ? list[list.length - 1].createdAt : null;
    const hasNext = page?.pageInfo?.hasNextPage;

    if (!hasNext) break;
    if (!oldest)  break;
    if (oldest < startUtc) break;

    after = page.pageInfo.endCursor;
  }

  // ここから「生成が失敗してもREADMEは上書き」方針
  let finalText;

  try {
    if (nodes.length === 0) {
      finalText = `昨日は投稿がなかったみたいだね！えらい、ちゃんと休息も取れてる！
昨日の松戸は${dominantWeather.icon}${dominantWeather.label}だったよ。お外でもストレッチできたかな？
今日の一言：背すじスッと、ニコッといこー！`;
    } else {
      const { source, usedChars } = buildPromptSource(nodes, {
        maxPerBodyChars: 600,
        maxTotalChars: 2500,
      });

      const system = `あなたは「体操のお兄さん」風の文章を書くプロです。
トーンは優しめで、軽いコメディ（ツッコミ）を入れてください。
誹謗中傷や攻撃的表現は避けてください。`;

      const user = `
昨日（JST: ${dayJst}）の松戸市の日中の天気は ${dominantWeather.label} でした。

以下は昨日（JST: ${dayJst}）に投稿されたGitHub Discussionsです。本文は抜粋で、長文は省略されています。
この内容を踏まえて、次を日本語で生成してください。

要件:
- 300文字前後（±80文字くらいはOK）
- 適切な位置で改行
- 「昨日のまとめ」に対する優しいツッコミ（コメディアン寄り）
- 天気にも一言触れてOK（無理に入れなくてもOK）
- 「今日の一言」（最後に「今日の一言：...」の形式で1文）
- 固有名詞やURLは無理に入れなくてOK（入れるなら1つまで）
- 出力は文章だけ（箇条書きや見出しは不要）
- コメディアンでユーモラスに
- あなたは体操のお兄さんです！元気もりもり！
Discussions（抜粋、合計 ${usedChars} 文字）:
${source}
`.trim();

      const text = await cfAiChat({
        accountId: CF_ACCOUNT_ID,
        apiToken:  CF_API_TOKEN,
        model:     CF_MODEL,
        messages: [
          { role: "system", content: system },
          { role: "user",   content: user   },
        ],
      });

      finalText = text;
    }
  } catch (e) {
    // 失敗時：READMEを「今日はお休み」文で上書き（運用重視）
    console.warn("Generation failed. Falling back to rest message.");
    console.warn(String(e?.message || e));

    finalText = `ごめんね、今日はお兄さんの声がちょっと裏返っちゃった！機械も準備体操が必要なんだ〜。
昨日の分はアーカイブにちゃんと残してあるから安心してね。
今日の一言：うまくいかない日こそ、ストレッチして笑って切り替えよう！`;
  }

  const newBlock = buildReadmeBlock({ dayJst, text: finalText, dominantWeather });

  const readme  = fs.readFileSync("README.md", "utf8");
  const updated = replaceBlock(readme, newBlock);
  fs.writeFileSync("README.md", updated, "utf8");

  console.log(`README updated for JST ${dayJst}. discussions=${nodes.length}`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
