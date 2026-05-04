(() => {
  const STORAGE_KEY = "fake_lang_barrier_v2";

  // === 170+ Countries (ISO 3166-1 alpha-2) ===
  const ALL_COUNTRIES = [
    "AF","AL","DZ","AD","AO","AR","AM","AU","AT","AZ","BS","BH","BD","BB","BY","BE","BZ","BJ","BT","BO","BA","BW","BR","BN","BG","BF","BI","KH","CM","CA",
    "CV","CF","TD","CL","CN","CO","KM","CG","CD","CR","CI","HR","CU","CY","CZ","DK","DJ","DM","DO","EC","EG","SV","GQ","ER","EE","ET","FJ","FI","FR",
    "GA","GM","GE","DE","GH","GR","GD","GT","GN","GW","GY","HT","HN","HU","IS","IN","ID","IR","IQ","IE","IL","IT","JM","JP","JO","KZ","KE","KI","KP","KR",
    "KW","KG","LA","LV","LB","LS","LR","LY","LI","LT","LU","MG","MW","MY","MV","ML","MT","MR","MU","MX","MD","MC","MN","ME","MA","MZ","MM","NA","NR","NP",
    "NL","NZ","NI","NE","NG","NO","OM","PK","PA","PG","PY","PE","PH","PL","PT","QA","RO","RU","RW","SA","SN","RS","SC","SL","SG","SK","SI","SB","SO",
    "ZA","ES","LK","SD","SR","SZ","SE","CH","SY","TW","TJ","TZ","TH","TL","TG","TO","TT","TN","TR","TM","UG","UA","AE","GB","US","UY","UZ","VE","VN",
    "YE","ZM","ZW"
  ];

  // === Country → Language overrides ===
  const COUNTRY_GROUPS = {
    ja: ["JP"],
    zh: ["CN","TW","HK","MO"],
    ko: ["KR","KP"],
    ru: ["RU","BY","KZ","KG","TJ","UZ"],
    uk: ["UA"],
    ar: ["SA","AE","QA","BH","KW","OM","YE","EG","SD","LY","TN","DZ","MA","JO","LB","SY","IQ","PS","MR","SO"],
    fa: ["IR","AF"],
    he: ["IL"],
    el: ["GR","CY"],
    tr: ["TR","AZ","TM"],
    hi: ["IN","NP"],
    bn: ["BD"],
    ur: ["PK"],
    th: ["TH"],
    vi: ["VN"],
    id: ["ID"],
    ms: ["MY","BN","SG"],
    de: ["DE","AT","CH","LI","LU"],
    fr: ["FR","BE","CI","SN","ML","NE","BF","BJ","TG","CM","GA","GN","CG","CD","MG","TD","DJ","KM","SC"],
    es: ["ES","MX","AR","CO","CL","PE","VE","EC","BO","UY","PY","CR","GT","SV","HN","NI","PA","DO","CU","GQ"],
    pt: ["PT","BR","AO","MZ","CV","GW","ST","TL"],
    it: ["IT","SM","VA"],
    nl: ["NL","SR"],
    sv: ["SE"],
    no: ["NO"],
    da: ["DK"],
    fi: ["FI"],
    pl: ["PL"],
    cs: ["CZ"],
    sk: ["SK"],
    hu: ["HU"],
    ro: ["RO","MD"],
    bg: ["BG"]
  };

  // === Build Country→Lang map (default to latin) ===
  const COUNTRY_LANG = Object.fromEntries(ALL_COUNTRIES.map(c => [c, "latin"]));
  Object.entries(COUNTRY_GROUPS).forEach(([lang, codes]) => {
    codes.forEach(code => COUNTRY_LANG[code] = lang);
  });

  // === Character sets ===
  const latinChars =
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ" +
    "áàâäãåāçčćďéèêëēěğíìîïīłñńóòôöõōřśšşťúùûüūýÿžźżÆæØøÅåßÐðÞþ";
  const cyrillicChars = Array.from({ length: 256 }, (_, i) => String.fromCharCode(0x0400 + i)).join("");
  const arabicChars = Array.from({ length: 256 }, (_, i) => String.fromCharCode(0x0600 + i)).join("");
  const hanChars = Array.from({ length: 1500 }, (_, i) => String.fromCharCode(0x4E00 + i)).join("");
  const devanagariChars = Array.from({ length: 128 }, (_, i) => String.fromCharCode(0x0900 + i)).join("");
  const thaiChars = Array.from({ length: 128 }, (_, i) => String.fromCharCode(0x0E00 + i)).join("");
  const hebrewChars = Array.from({ length: 96 }, (_, i) => String.fromCharCode(0x0590 + i)).join("");
  const greekChars = Array.from({ length: 96 }, (_, i) => String.fromCharCode(0x0370 + i)).join("");
  const hangulChars = Array.from({ length: 512 }, (_, i) => String.fromCharCode(0xAC00 + i)).join("");

  // === Language configs ===
  const LANG_META = {
    ja: { label: "日本語 (Safe Zone)", chars: null, font: "sans-serif", dir: "ltr" },
    en: { label: "English (Alphabet)", chars: latinChars, font: "sans-serif", dir: "ltr" },
    latin: { label: "Latin (Alphabet)", chars: latinChars, font: "sans-serif", dir: "ltr" },
    es: { label: "Español", chars: latinChars, font: "sans-serif", dir: "ltr" },
    fr: { label: "Français", chars: latinChars, font: "sans-serif", dir: "ltr" },
    pt: { label: "Português", chars: latinChars, font: "sans-serif", dir: "ltr" },
    de: { label: "Deutsch", chars: latinChars, font: "sans-serif", dir: "ltr" },
    it: { label: "Italiano", chars: latinChars, font: "sans-serif", dir: "ltr" },
    nl: { label: "Nederlands", chars: latinChars, font: "sans-serif", dir: "ltr" },
    sv: { label: "Svenska", chars: latinChars, font: "sans-serif", dir: "ltr" },
    no: { label: "Norsk", chars: latinChars, font: "sans-serif", dir: "ltr" },
    da: { label: "Dansk", chars: latinChars, font: "sans-serif", dir: "ltr" },
    fi: { label: "Suomi", chars: latinChars, font: "sans-serif", dir: "ltr" },
    pl: { label: "Polski", chars: latinChars, font: "sans-serif", dir: "ltr" },
    cs: { label: "Čeština", chars: latinChars, font: "sans-serif", dir: "ltr" },
    sk: { label: "Slovenčina", chars: latinChars, font: "sans-serif", dir: "ltr" },
    hu: { label: "Magyar", chars: latinChars, font: "sans-serif", dir: "ltr" },
    ro: { label: "Română", chars: latinChars, font: "sans-serif", dir: "ltr" },
    bg: { label: "Български", chars: cyrillicChars, font: "'Ruslan Display', cursive", dir: "ltr" },
    ru: { label: "Русский", chars: cyrillicChars, font: "'Ruslan Display', cursive", dir: "ltr" },
    uk: { label: "Українська", chars: cyrillicChars, font: "'Ruslan Display', cursive", dir: "ltr" },
    ar: { label: "العربية", chars: arabicChars, font: "'Amiri', serif", dir: "rtl" },
    fa: { label: "فارسی", chars: arabicChars, font: "'Amiri', serif", dir: "rtl" },
    he: { label: "עברית", chars: hebrewChars, font: "'Noto Serif Hebrew', serif", dir: "rtl" },
    el: { label: "Ελληνικά", chars: greekChars, font: "'Noto Serif Greek', serif", dir: "ltr" },
    zh: { label: "中文", chars: hanChars, font: "'Zhi Mang Xing', cursive", dir: "ltr" },
    ko: { label: "한국어", chars: hangulChars, font: "'Noto Sans KR', sans-serif", dir: "ltr" },
    hi: { label: "हिन्दी", chars: devanagariChars, font: "'Noto Sans Devanagari', sans-serif", dir: "ltr" },
    bn: { label: "বাংলা", chars: devanagariChars, font: "'Noto Sans Devanagari', sans-serif", dir: "ltr" },
    ur: { label: "اردو", chars: arabicChars, font: "'Amiri', serif", dir: "rtl" },
    th: { label: "ไทย", chars: thaiChars, font: "'Noto Sans Thai', sans-serif", dir: "ltr" },
    vi: { label: "Tiếng Việt", chars: latinChars, font: "sans-serif", dir: "ltr" },
    id: { label: "Bahasa Indonesia", chars: latinChars, font: "sans-serif", dir: "ltr" },
    ms: { label: "Bahasa Melayu", chars: latinChars, font: "sans-serif", dir: "ltr" }
  };

  const LANG_ORDER = [
    "ja","en","latin","es","fr","pt","de","it","nl","sv","no","da","fi","pl","cs","sk","hu","ro","bg",
    "ru","uk","ar","fa","he","el","zh","ko","hi","bn","ur","th","vi","id","ms"
  ];

  // === Load fonts (once) ===
  const fontsHref =
    "https://fonts.googleapis.com/css2?family=Amiri&family=Ruslan+Display&family=Zhi+Mang+Xing&family=Noto+Sans+KR&family=Noto+Sans+Thai&family=Noto+Sans+Devanagari&family=Noto+Serif+Hebrew&family=Noto+Serif+Greek&display=swap";
  if (!document.querySelector(`link[href="${fontsHref}"]`)) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = fontsHref;
    document.head.appendChild(link);
  }

  // === Text node collection ===
  const EXCLUDE_TAGS = new Set(["SCRIPT","STYLE","TEXTAREA","INPUT","OPTION","SELECT","NOSCRIPT"]);
  const originalMap = new WeakMap();
  const textNodes = new Set();
  let currentLang = "ja";

  function isSkippable(node) {
    if (!node || !node.parentElement) return true;
    const tag = node.parentElement.tagName;
    if (EXCLUDE_TAGS.has(tag)) return true;
    if (node.parentElement.closest("[contenteditable='true']")) return true;
    return false;
  }

  function collectTextNodes(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        if (isSkippable(node)) return NodeFilter.FILTER_REJECT;
        if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (!originalMap.has(node)) originalMap.set(node, node.nodeValue);
      textNodes.add(node);
    }
  }
  const cache = new Map();
  function scramble(text, charSet, langKey) {
    return text.split("").map(char => {
      if (/[\s.,!?;:！？。、「」()0-9]/.test(char)) return char;
      const cacheKey = `${langKey}|${char}`;
      if (cache.has(cacheKey)) return cache.get(cacheKey);

      const code = char.charCodeAt(0);
      let hash = ((code << 5) - code) + code;
      const index = Math.abs(hash) % charSet.length;
      const mapped = charSet[index];

      cache.set(cacheKey, mapped);
      return mapped;
    }).join("");
  }

  // === Apply language ===
  function applyLanguage(lang) {
    const config = LANG_META[lang] || LANG_META.ja;
    currentLang = lang;

    document.body.style.fontFamily = config.font;
    document.body.style.direction = config.dir;

    textNodes.forEach(node => {
      const original = originalMap.get(node) || node.nodeValue;
      if (lang === "ja" || !config.chars) {
        node.nodeValue = original;
      } else {
        node.nodeValue = scramble(original, config.chars, lang);
      }
    });

    localStorage.setItem(STORAGE_KEY, lang);
  }

  // === Language detection ===
  function detectLang() {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) return saved;

    const raw = (navigator.language || navigator.userLanguage || "ja").toLowerCase();
    const parts = raw.split(/[-_]/);
    const lang = parts[0];
    const country = (parts[1] || "").toUpperCase();

    if (country && COUNTRY_LANG[country]) return COUNTRY_LANG[country];
    if (LANG_META[lang]) return lang;
    return "ja";
  }

  // === UI ===
  function buildSelector() {
    const container = document.createElement("div");
    container.id = "barrier-container";
    container.style.cssText = `
      position: fixed; top: 10px; right: 10px;
      background: rgba(240,240,240,0.95); padding: 12px;
      border: 2px solid #333; box-shadow: 2px 2px 10px rgba(0,0,0,0.2);
      z-index: 100000; font-family: sans-serif;
    `;

    const label = document.createElement("label");
    label.htmlFor = "lang-select";
    label.textContent = "Language (絶望): ";
    container.appendChild(label);

    const select = document.createElement("select");
    select.id = "lang-select";
    LANG_ORDER.forEach(key => {
      if (!LANG_META[key]) return;
      const opt = document.createElement("option");
      opt.value = key;
      opt.textContent = LANG_META[key].label;
      select.appendChild(opt);
    });
    select.addEventListener("change", e => applyLanguage(e.target.value));

    container.appendChild(select);
    document.body.appendChild(container);
  }

  // === Init ===
  function init() {
    collectTextNodes(document.body);
    buildSelector();

    const initial = detectLang();
    const select = document.getElementById("lang-select");
    if (select) select.value = initial;
    applyLanguage(initial);

    // Observe dynamic content
    const observer = new MutationObserver(mutations => {
      mutations.forEach(m => {
        m.addedNodes.forEach(node => {
          if (node.nodeType === 1 || node.nodeType === 11) {
            collectTextNodes(node);
          } else if (node.nodeType === 3) {
            collectTextNodes(node.parentNode || document.body);
          }
        });
      });
      if (currentLang !== "ja") applyLanguage(currentLang);
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
