/*
 Copyright (C) 2026 htvoffcial
 SPDX-License-Identifier: SSPL-1.0
*/

import fs from "node:fs";
import path from "node:path";

const GH_TOKEN = process.env.GH_TOKEN;
const REPOSITORY = process.env.GITHUB_REPOSITORY;

if (!GH_TOKEN) throw new Error("GH_TOKEN is missing");
if (!REPOSITORY) throw new Error("GITHUB_REPOSITORY is missing");

const [owner, repo] = REPOSITORY.split("/");
if (!owner || !repo) throw new Error(`Invalid GITHUB_REPOSITORY: ${REPOSITORY}`);

const POS_TAGS = [
  "形容動詞",
  "接続詞",
  "形容詞",
  "代名詞",
  "連体詞",
  "感動詞",
  "副詞",
  "動詞",
  "名詞",
];

// 一段動詞に見えるが五段動詞である代表的な例外
const GODAN_EXCEPTIONS = new Set([
  "走る", "帰る", "切る", "知る", "入る", "要る", "減る", "喋る", "限る", "握る"
]);

/**
 * 動詞の簡易活用関数（自然な接続にするための変換）
 */
function conjugateVerb(verb, form) {
  if (!verb || verb.length < 1) return verb;

  // 不規則動詞の処理
  if (verb === "する" || verb.endsWith("する")) {
    const base = verb.slice(0, -2);
    if (form === "連用" || form === "未然") return base + "し";
    if (form === "て" || form === "た") return base + "した";
    return verb;
  }
  if (verb === "来る" || verb === "くる") {
    const isKanji = verb === "来る";
    if (form === "連用") return isKanji ? "来" : "き";
    if (form === "未然") return isKanji ? "来" : "こ";
    if (form === "て" || form === "た") return isKanji ? "来た" : "きた";
    return verb;
  }
  if (verb === "行く" && (form === "て" || form === "た")) {
    return "行った";
  }

  const lastChar = verb.slice(-1);
  const stem = verb.slice(0, -1);
  const prevChar = verb.length >= 2 ? verb.slice(-2, -1) : "";

  // 一段動詞か五段動詞かの判定
  const isIchidan =
    lastChar === "る" &&
    /[いきしちにひみりえけせてねへめれ]/.test(prevChar) &&
    !GODAN_EXCEPTIONS.has(verb);

  if (isIchidan) {
    if (form === "連用" || form === "未然") return stem;
    if (form === "て" || form === "た") return stem + "た";
    return verb;
  }

  // 五段動詞の活用
  if (form === "連用") {
    const map = { う: "い", く: "き", ぐ: "ぎ", す: "し", つ: "ち", ぬ: "に", ぶ: "び", む: "み", る: "り" };
    return stem + (map[lastChar] || lastChar);
  }
  if (form === "未然") {
    const map = { う: "わ", く: "か", ぐ: "が", す: "さ", つ: "た", ぬ: "な", ぶ: "ば", む: "ま", る: "ら" };
    return stem + (map[lastChar] || lastChar);
  }
  if (form === "て" || form === "た") {
    if (["う", "つ", "る"].includes(lastChar)) return stem + "った";
    if (["む", "ぶ", "ぬ"].includes(lastChar)) return stem + "んだ";
    if (lastChar === "く") return stem + "いた";
    if (lastChar === "ぐ") return stem + "いだ";
    if (lastChar === "す") return stem + "した";
  }

  return verb;
}

/**
 * 形容動詞の語幹整形と活用
 */
function conjugateNaAdjective(word, nextChars) {
  let stem = word.endsWith("だ") ? word.slice(0, -1) : word;

  // 後ろが「名詞」や一般的な名詞フレーズなら「〜な」に接続
  if (/^(\{?名詞\}?|[^\s、。！？だで])/.test(nextChars)) {
    return `${stem}な`;
  }
  // 後ろが「です」「でした」なら語幹のまま
  if (/^(です|でした)/.test(nextChars)) {
    return stem;
  }
  // 文末などで直接終わる場合
  if (/^[。！？\s]*$/.test(nextChars)) {
    return `${stem}だ`;
  }
  return stem;
}

function pickRandomUnique(list, usedSet) {
  if (!Array.isArray(list) || list.length === 0) return "";
  const available = list.filter((w) => !usedSet.has(w));
  const pool = available.length > 0 ? available : list;
  const index = Math.floor(Math.random() * pool.length);
  const picked = pool[index] || "";
  usedSet.add(picked);
  return picked;
}

function loadTemplates(baseDir) {
  const filePath = path.join(baseDir, ".github", "assets", "jpt_goj.csv");
  const raw = fs.readFileSync(filePath, "utf8");
  return raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"));
}

function loadWordsByPos(baseDir) {
  const filePath = path.join(baseDir, ".github", "assets", "jpt_cps.csv");
  const raw = fs.readFileSync(filePath, "utf8");
  const lines = raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);

  const map = new Map();

  for (const line of lines.slice(1)) {
    const commaIndex = line.lastIndexOf(",");
    if (commaIndex <= 0) continue;

    const word = line.slice(0, commaIndex).trim();
    const pos = line.slice(commaIndex + 1).trim();

    if (!word || !pos) continue;

    if (!map.has(pos)) map.set(pos, []);
    map.get(pos).push(word);
  }

  return map;
}

/**
 * テンプレート置換（シングルパス正規表現＆コンテキストに応じた自動活用）
 */
function fillTemplate(template, wordsByPos) {
  const usedWords = new Set();

  // {名詞} または 名詞 のどちらの表記にも対応
  const pattern = new RegExp(
    `\\{?(${POS_TAGS.join("|")})(?::([a-zA-Z0-9_]+))?\\}?`,
    "g"
  );

  // シングルパスで置換し、多重置換バグを防止
  let result = template.replace(pattern, (match, pos, explicitForm, offset, fullStr) => {
    const words = wordsByPos.get(pos) || [];
    let word = pickRandomUnique(words, usedWords);
    if (!word) return match;

    const remaining = fullStr.slice(offset + match.length);

    // 1. 動詞の自動活用
    if (pos === "動詞") {
      if (explicitForm) {
        word = conjugateVerb(word, explicitForm);
      } else if (/^(ます|ました|ません|たい|つつ|ながら|そう)/.test(remaining)) {
        word = conjugateVerb(word, "連用");
      } else if (/^(て|た|だ)/.test(remaining)) {
        word = conjugateVerb(word, "て");
      } else if (/^(ない|なかった|れる|られる|せる|させる)/.test(remaining)) {
        word = conjugateVerb(word, "未然");
      }
    }

    // 2. 形容動詞の自動調整
    if (pos === "形容動詞") {
      word = conjugateNaAdjective(word, remaining);
    }

    return word;
  });

  // 不自然な助詞の重なりや空白のクリーンアップ
  result = result
    .replace(/[ \t]+/g, " ")
    .replace(/をを/g, "を")
    .replace(/がが/g, "が")
    .replace(/はは/g, "は")
    .replace(/、、+/g, "、")
    .trim();

  return result;
}

/**
 * より人間らしく自然なディスカッションタイトルの生成
 */
function buildTitle(sentence) {
  const clean = sentence.trim().replace(/\s+/g, " ");

  // 35文字以下なら、そのまま自然なタイトルとして採用
  if (clean.length <= 35) {
    return clean;
  }

  // 最初の1文（「。」や「！？\n」まで）を取得
  const firstSentence = clean.split(/[。！？!?\n]/)[0]?.trim();
  if (firstSentence && firstSentence.length >= 5 && firstSentence.length <= 40) {
    // 疑問文で終わっている場合はそのまま活用
    if (clean.includes("？") || clean.includes("?")) {
      return `${firstSentence}？`;
    }
    return `${firstSentence}`;
  }

  // 長い場合のフォールバック（不自然に文字を切らず、語尾に…を付与）
  return `${clean.slice(0, 32)}…`;
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

  const json = await res.json().catch(() => null);

  if (!res.ok || json?.errors?.length) {
    throw new Error(`GraphQL failed: ${res.status} ${JSON.stringify(json)}`);
  }

  return json.data;
}

async function resolveCategoryAndRepoId() {
  const query = `
    query($owner: String!, $repo: String!) {
      repository(owner: $owner, name: $repo) {
        id
        discussionCategories(first: 50) {
          nodes {
            id
            name
            emoji
          }
        }
      }
    }
  `;

  const data = await ghGraphql(query, { owner, repo });
  const repository = data?.repository;

  if (!repository?.id) {
    throw new Error("repository.id was not found");
  }

  const categories = repository.discussionCategories?.nodes || [];
  const zatsudan = categories.find((c) => String(c?.name || "").includes("雑談"));

  if (!zatsudan?.id) {
    throw new Error("Discussion category '雑談' was not found");
  }

  return {
    repositoryId: repository.id,
    categoryId: zatsudan.id,
  };
}

async function createDiscussion({ repositoryId, categoryId, title, body }) {
  const mutation = `
    mutation($repositoryId: ID!, $categoryId: ID!, $title: String!, $body: String!) {
      createDiscussion(input: {
        repositoryId: $repositoryId,
        categoryId: $categoryId,
        title: $title,
        body: $body
      }) {
        discussion {
          id
          url
          title
        }
      }
    }
  `;

  const data = await ghGraphql(mutation, {
    repositoryId,
    categoryId,
    title,
    body,
  });

  return data?.createDiscussion?.discussion;
}

async function main() {
  const baseDir = process.cwd();

  const templates = loadTemplates(baseDir);
  if (templates.length === 0) throw new Error("No templates found in jpt_goj.csv");

  const wordsByPos = loadWordsByPos(baseDir);
  const template = pickRandomUnique(templates, new Set());
  const sentence = fillTemplate(template, wordsByPos);
  const title = buildTitle(sentence);

  const { repositoryId, categoryId } = await resolveCategoryAndRepoId();

  const discussion = await createDiscussion({
    repositoryId,
    categoryId,
    title,
    body: sentence,
  });

  if (!discussion?.url) {
    throw new Error("Failed to create discussion");
  }

  console.log(`Created discussion: ${discussion.url}`);
  console.log(`Title: ${discussion.title}`);
  console.log(`Body: ${sentence}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
