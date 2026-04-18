import fs from "node:fs";
import path from "node:path";

const START = "<!-- DISCUSS_COACH_START -->";
const END = "<!-- DISCUSS_COACH_END -->";

function extractBetween(text) {
  const startIdx = text.indexOf(START);
  const endIdx = text.indexOf(END);
  if (startIdx === -1 || endIdx === -1 || endIdx <= startIdx) return null;
  return text.slice(startIdx + START.length, endIdx).trim();
}

function extractSourceDateJST(text) {
  // README中にある: **対象日（JST）:** 2026-04-17
  const m = text.match(/\*\*対象日（JST）:\*\*\s*([0-9]{4}-[0-9]{2}-[0-9]{2})/);
  return m?.[1] ?? "";
}

async function deeplTranslate(text, { apiKey, baseUrl }) {
  if (!apiKey) throw new Error("Missing DEEPL_API_KEY secret");

  // baseUrlは secrets に入れるのが安全（Free/Pro切替）
  const endpoint = (baseUrl || "https://api-free.deepl.com").replace(/\/$/, "") + "/v2/translate";

  const params = new URLSearchParams();
  params.append("text", text);
  params.append("target_lang", "EN"); // 例: "EN" or "EN-US" or "EN-GB"
  // params.append("source_lang", "JA"); // 必要なら固定
  params.append("preserve_formatting", "1");

  // DeepL API: Authorization: DeepL-Auth-Key xxx
  const res = await fetch(endpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Authorization: `DeepL-Auth-Key ${apiKey}`,
    },
    body: params.toString(),
  });

  if (!res.ok) {
    const body = await res.text();
    throw new Error(`DeepL API error: ${res.status} ${body}`);
  }

  const data = await res.json();
  const translated = data?.translations?.[0]?.text;
  if (!translated) throw new Error("DeepL returned no translation text");
  return String(translated).trim();
}

async function main() {
  const readmePath = path.resolve("README.md");
  const src = fs.readFileSync(readmePath, "utf8");

  const inner = extractBetween(src);
  if (!inner) throw new Error("Could not find DISCUSS_COACH markers in README.md");

  const sourceDate = extractSourceDateJST(inner);

  const english = await deeplTranslate(inner, {
    apiKey: process.env.DEEPL_API_KEY,
    baseUrl: process.env.DEEPL_API_BASE_URL,
  });

  const outDir = path.resolve(".github/English");
  fs.mkdirSync(outDir, { recursive: true });

  const outPath = path.join(outDir, "README.md");

  const headerLines = [
    START,
    "## Discuss Summary (Gymnastics Coach)",
    sourceDate ? `**Source date (JST):** ${sourceDate}` : "",
    "",
  ].filter(Boolean);

  const out = `${headerLines.join("\n")}
${english}
${END}
`;

  fs.writeFileSync(outPath, out, "utf8");
  console.log(`Wrote ${outPath}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
