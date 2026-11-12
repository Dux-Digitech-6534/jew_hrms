// Runtime language layer for JEW HRMS: English (default, untouched) / हिंदी / Hinglish.
// It translates the TEXT THE USER SEES (text nodes + placeholder/title/aria-label/alt) from a dictionary, so every
// screen is covered without touching each component, and anything not in the dictionary simply stays English.
// Switching back to English restores the original text exactly. Elements with data-no-tr are never touched.
import { DICT_1 } from "./i18n-dict-1";
import { DICT_2 } from "./i18n-dict-2";
import { DICT_3 } from "./i18n-dict-3";
import { DICT_4 } from "./i18n-dict-4";

export type Lang = "en" | "hi" | "hing";
const STORE_KEY = "jew-lang";
const ALL: Array<[string, string, string]> = [...DICT_1, ...DICT_2, ...DICT_3, ...DICT_4];
const clean = (s: string) => s.replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();

const HI = new Map<string, string>(), HING = new Map<string, string>(), HI_L = new Map<string, string>(), HING_L = new Map<string, string>();
for (const [en, hi, hing] of ALL) {
  const k = clean(en);
  if (!HI.has(k)) { HI.set(k, hi); HING.set(k, hing); }
  const kl = k.toLowerCase();
  // short words (in / out / IN / May ...) are matched case-sensitively only
  if (k.length > 4 && !HI_L.has(kl)) { HI_L.set(kl, hi); HING_L.set(kl, hing); }
}

const MONTHS: Record<string, [string, string]> = {
  january: ["जनवरी", "January"], february: ["फ़रवरी", "February"], march: ["मार्च", "March"], april: ["अप्रैल", "April"], may: ["मई", "May"], june: ["जून", "June"],
  july: ["जुलाई", "July"], august: ["अगस्त", "August"], september: ["सितंबर", "September"], october: ["अक्टूबर", "October"], november: ["नवंबर", "November"], december: ["दिसंबर", "December"],
  jan: ["जन", "Jan"], feb: ["फ़र", "Feb"], mar: ["मार्च", "Mar"], apr: ["अप्रै", "Apr"], jun: ["जून", "Jun"], jul: ["जुला", "Jul"], aug: ["अग", "Aug"], sep: ["सित", "Sep"], sept: ["सित", "Sept"], oct: ["अक्टू", "Oct"], nov: ["नव", "Nov"], dec: ["दिस", "Dec"],
};
const DAYS: Record<string, [string, string]> = {
  sunday: ["रविवार", "Sunday"], monday: ["सोमवार", "Monday"], tuesday: ["मंगलवार", "Tuesday"], wednesday: ["बुधवार", "Wednesday"], thursday: ["गुरुवार", "Thursday"], friday: ["शुक्रवार", "Friday"], saturday: ["शनिवार", "Saturday"],
  sun: ["रवि", "Sun"], mon: ["सोम", "Mon"], tue: ["मंगल", "Tue"], wed: ["बुध", "Wed"], thu: ["गुरु", "Thu"], fri: ["शुक्र", "Fri"], sat: ["शनि", "Sat"],
};
const DATE_WORDS = new RegExp("^(?:(?:" + Object.keys(DAYS).join("|") + ")\\.?,? )?(?:\\d{1,2} )?(?:" + Object.keys(MONTHS).join("|") + ")\\.?,?(?: \\d{1,2})?(?:,? \\d{4})?$", "i");

type Pat = { re: RegExp; hi: (m: RegExpMatchArray) => string; hing: (m: RegExpMatchArray) => string };
const PATTERNS: Pat[] = [
  { re: /^(\d+) days? left$/, hi: (m) => `${m[1]} दिन बाकी`, hing: (m) => `${m[1]} din baaki` },
  { re: /^Max (\d+) days at a time$/, hi: (m) => `एक बार में अधिकतम ${m[1]} दिन`, hing: (m) => `Ek baar me max ${m[1]} din` },
  { re: /^Balance: (.+) days? remaining\.$/, hi: (m) => `बैलेंस: ${m[1]} दिन बाकी।`, hing: (m) => `Balance: ${m[1]} din baaki.` },
  { re: /^(\d+(?:\.\d+)?) days?$/, hi: (m) => `${m[1]} दिन`, hing: (m) => `${m[1]} din` },
  { re: /^(\d+(?:\.\d+)?) hrs?$/, hi: (m) => `${m[1]} घंटे`, hing: (m) => `${m[1]} ghante` },
  { re: /^(\d+) min(?:s|utes)?$/, hi: (m) => `${m[1]} मिनट`, hing: (m) => `${m[1]} min` },
  { re: /^(\d+) m radius$/, hi: (m) => `${m[1]} मीटर दायरा`, hing: (m) => `${m[1]} m radius` },
  { re: /^(\d+) pending$/i, hi: (m) => `${m[1]} लंबित`, hing: (m) => `${m[1]} pending` },
  { re: /^(\d+) On Duty$/, hi: (m) => `${m[1]} ऑन ड्यूटी`, hing: (m) => `${m[1]} On Duty` },
];

const lookup = (lang: Lang, key: string): string | null => {
  const exact = lang === "hi" ? HI : HING;
  const low = lang === "hi" ? HI_L : HING_L;
  return exact.get(key) ?? low.get(key.toLowerCase()) ?? null;
};

const translateCore = (lang: Lang, text: string): string | null => {
  const key = clean(text);
  if (!key || !/[A-Za-z]/.test(key)) return null;
  const hit = lookup(lang, key);
  if (hit != null) return hit;
  for (const p of PATTERNS) { const m = key.match(p.re); if (m) return lang === "hi" ? p.hi(m) : p.hing(m); }
  // "<phrase> (3)" countdowns, "<phrase>:" / "<phrase>," / "<phrase>." / "<phrase>…" with a trailing mark
  let m = key.match(/^(.*?) \((\d+)\)$/);
  if (m) { const b = lookup(lang, m[1]); if (b != null) return `${b} (${m[2]})`; }
  m = key.match(/^(.*?)([,:.…!?]+)$/);
  if (m) { const b = lookup(lang, m[1]); if (b != null) return b + m[2]; }
  // "In 09:30" style  /  "Status: Approved"  -> translate the label before a colon when the rest is not text we know
  m = key.match(/^([A-Za-z][A-Za-z \/&-]{1,40}): (.+)$/);
  if (m) { const b = lookup(lang, m[1] + ":") ?? lookup(lang, m[1]); const rest = translateCore(lang, m[2]); if (b != null) return `${b.replace(/[:：]$/, "")}: ${rest ?? m[2]}`; }
  // month / weekday words inside a date-looking string
  if (DATE_WORDS.test(key)) {
    const i = lang === "hi" ? 0 : 1;
    return key.replace(/[A-Za-z]+/g, (w) => { const l = w.toLowerCase(); const r = MONTHS[l] || DAYS[l]; return r ? r[i] : w; });
  }
  return null;
};

export const translateText = (lang: Lang, raw: string): string | null => {
  if (lang === "en") return null;
  const lead = raw.match(/^\s*/)![0], trail = raw.match(/\s*$/)![0];
  const out = translateCore(lang, raw);
  return out == null ? null : lead + out + trail;
};

// ------------------------------------------------------------------------------------------------ language state
let current: Lang = "en";
const listeners = new Set<(l: Lang) => void>();
try { const s = localStorage.getItem(STORE_KEY); if (s === "hi" || s === "hing" || s === "en") current = s; } catch { /* storage blocked: stay English */ }
export const getLang = (): Lang => current;
export const subscribeLang = (fn: (l: Lang) => void) => { listeners.add(fn); return () => { listeners.delete(fn); }; };

// ------------------------------------------------------------------------------------------------ DOM engine
const ATTRS = ["placeholder", "aria-label", "title", "alt"];
const textRec = new WeakMap<Text, { orig: string; out: string }>();
const attrRec = new WeakMap<Element, Record<string, { orig: string; out: string }>>();
const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "TEXTAREA", "NOSCRIPT", "CODE", "PRE"]);
const skipped = (el: Element | null): boolean => { for (let e = el; e; e = e.parentElement) { if (SKIP_TAGS.has(e.tagName) || e.hasAttribute("data-no-tr") || (e as HTMLElement).isContentEditable) return true; } return false; };

const doText = (node: Text) => {
  const cur = node.nodeValue ?? "";
  const rec = textRec.get(node);
  const orig = rec && cur === rec.out ? rec.orig : cur;
  if (current === "en") { if (rec && cur === rec.out) { node.nodeValue = rec.orig; } textRec.delete(node); return; }
  if (skipped(node.parentElement)) return;
  const t = translateText(current, orig);
  if (t == null) { textRec.delete(node); return; }
  if (t !== cur) { textRec.set(node, { orig, out: t }); node.nodeValue = t; } else if (!rec) textRec.set(node, { orig, out: t });
};
const doAttr = (el: Element, name: string) => {
  const cur = el.getAttribute(name);
  if (cur == null) return;
  const recs = attrRec.get(el) || {};
  const rec = recs[name];
  const orig = rec && cur === rec.out ? rec.orig : cur;
  if (current === "en") { if (rec && cur === rec.out) el.setAttribute(name, rec.orig); delete recs[name]; return; }
  if (skipped(el)) return;
  const t = translateText(current, orig);
  if (t == null) { delete recs[name]; return; }
  if (t !== cur) { recs[name] = { orig, out: t }; attrRec.set(el, recs); el.setAttribute(name, t); }
};
const walk = (root: Node) => {
  if (root.nodeType === Node.TEXT_NODE) { doText(root as Text); return; }
  if (root.nodeType !== Node.ELEMENT_NODE) return;
  const el = root as Element;
  for (const a of ATTRS) if (el.hasAttribute(a)) doAttr(el, a);
  const tw = document.createTreeWalker(el, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
  let n: Node | null = tw.nextNode();
  while (n) {
    if (n.nodeType === Node.TEXT_NODE) doText(n as Text);
    else for (const a of ATTRS) if ((n as Element).hasAttribute(a)) doAttr(n as Element, a);
    n = tw.nextNode();
  }
};

let pending = new Set<Node>();
let scheduled = false;
const flush = () => {
  scheduled = false;
  const batch = pending; pending = new Set();
  batch.forEach((n) => { if (n.isConnected) walk(n); });
};
// setTimeout (not requestAnimationFrame): rAF is paused in background / hidden WebViews, which would leave new screens untranslated.
const schedule = (n: Node) => { pending.add(n); if (!scheduled) { scheduled = true; window.setTimeout(flush, 20); } };

let started = false;
export const initI18n = () => {
  if (started || typeof document === "undefined") return;
  started = true;
  document.documentElement.lang = current === "hi" ? "hi" : "en";
  const start = () => {
    const obs = new MutationObserver((records) => {
      for (const r of records) {
        if (r.type === "characterData") { if (r.target.nodeType === Node.TEXT_NODE) doText(r.target as Text); }
        else if (r.type === "attributes") { if (r.attributeName) doAttr(r.target as Element, r.attributeName); }
        else r.addedNodes.forEach((n) => schedule(n));
      }
    });
    obs.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
    if (current !== "en") schedule(document.body);
  };
  if (document.body) start(); else document.addEventListener("DOMContentLoaded", start, { once: true });
};

export const setLang = (lang: Lang) => {
  if (lang === current) return;
  current = lang;
  try { localStorage.setItem(STORE_KEY, lang); } catch { /* ignore */ }
  document.documentElement.lang = lang === "hi" ? "hi" : "en";
  if (document.body) walk(document.body);
  listeners.forEach((fn) => fn(lang));
};
