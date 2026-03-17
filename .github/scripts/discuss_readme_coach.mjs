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

const CF_MODEL = "@cf/google/gemma-3-12b-it";

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
  const endUtc = new Date(`${dayJst}T23:59:59+09:00`).toISOString().replace(/\.\d{3}Z$/, "Z");

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
  const text =
    json?.result?.response ??
    json?.result?.output_text ??
    json?.result?.text ??
    null;

  if (!text) {
    throw new Error(`Cloudflare AI: unknown response shape: ${JSON.stringify(json).slice(0, 800)}`);
  }
  return String(text).trim();
}

function replaceBlock(readme, newBlock) {
  const start = "<!-- DISCUSS_COACH_START -->";
  const end = "<!-- DISCUSS_COACH_END -->";
  const s = readme.indexOf(start);
  const e = readme.indexOf(end);
  if (s === -1 || e === -1 || e < s) {
    throw new Error("README.md does not contain DISCUSS_COACH_START/END markers");
  }
  const before = readme.slice(0, s + start.length);
  const after = readme.slice(e);
  return `${before}\n${newBlock}\n${after}`;
}

function buildReadmeBlock({ dayJst, text }) {
  const header = `## Discussまとめ（体操のお兄さん）
**対象日（JST）:** ${dayJst}
`;
  return `${header}\n${text}\n`;
}

async function main() {
  const { dayJst, startUtc, endUtc } = getYesterdayJstRangeUtc();

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
    if (!oldest) break;
    if (oldest < startUtc) break;

    after = page.pageInfo.endCursor;
  }

  // ここから「生成が失敗してもREADMEは上書き」方針
  let finalText;

  try {
    if (nodes.length === 0) {
      finalText = `昨日は投稿がなかったみたいだね！えらい、ちゃんと休息も取れてる！
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
以下は昨日（JST: ${dayJst}）に投稿されたGitHub Discussionsです。本文は抜粋で、長文は省略されています。
この内容を踏まえて、次を日本語で生成してください。

要件:
- 300文字前後（±80文字くらいはOK）
- 「昨日のまとめ」に対する優しいツッコミ（コメディアン寄り）
- 「今日の一言」（最後に「今日の一言：...」の形式で1文）
- 固有名詞やURLは無理に入れなくてOK（入れるなら1つまで）
- 出力は文章だけ（箇条書きや見出しは不要）
- コメディアンでユーモラスに
Discussions（抜粋、合計 ${usedChars} 文字）:
${source}
`.trim();

      const text = await cfAiChat({
        accountId: CF_ACCOUNT_ID,
        apiToken: CF_API_TOKEN,
        model: CF_MODEL,
        messages: [
          { role: "system", content: system },
          { role: "user", content: user },
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

  const newBlock = buildReadmeBlock({ dayJst, text: finalText });

  const readme = fs.readFileSync("README.md", "utf8");
  const updated = replaceBlock(readme, newBlock);
  fs.writeFileSync("README.md", updated, "utf8");

  console.log(`README updated for JST ${dayJst}. discussions=${nodes.length}`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
