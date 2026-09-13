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

const POS_TAGS = ["形容動詞", "接続詞", "形容詞", "代名詞", "連体詞", "感動詞", "副詞", "動詞", "名詞"];

function pickRandom(list) {
  if (!Array.isArray(list) || list.length === 0) return "";
  const index = Math.floor(Math.random() * list.length);
  return list[index] || "";
}

function loadTemplates(baseDir) {
  const filePath = path.join(baseDir, ".github", "assets", "jpt_goj.csv");
  const raw = fs.readFileSync(filePath, "utf8");
  return raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
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

function fillTemplate(template, wordsByPos) {
  let result = template;

  for (const pos of POS_TAGS) {
    const words = wordsByPos.get(pos) || [];
    const pattern = new RegExp(pos, "g");
    result = result.replace(pattern, () => {
      const word = pickRandom(words);
      return word || pos;
    });
  }

  return result.replace(/[ \t]+/g, " ").trim();
}

function buildTitle(sentence) {
  const chunks = sentence
    .replace(/[。！？]+$/g, "")
    .split(/[、。！？\s]+/)
    .map((x) => x.trim())
    .filter(Boolean);

  const selected = chunks.slice(0, 3).map((x) => x.slice(0, 8));

  let title = selected.length > 0 ? `${selected.join("・")}の雑談` : `${sentence.slice(0, 20)}の雑談`;

  if (title.length > 60) title = `${title.slice(0, 60)}…`;
  return title;
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
  const template = pickRandom(templates);
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
