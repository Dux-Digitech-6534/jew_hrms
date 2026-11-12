import { useEffect, useState } from "react";
import { getLang, setLang, subscribeLang, type Lang } from "./i18n";

const OPTIONS: Array<{ id: Lang; label: string; sub: string }> = [
  { id: "en", label: "English", sub: "English" },
  { id: "hi", label: "हिंदी", sub: "Hindi" },
  { id: "hing", label: "Hinglish", sub: "Roman Hindi" },
];

// Three-way language switch. data-no-tr keeps the language names themselves out of the translator.
export function LangPicker({ compact = false }: { compact?: boolean }) {
  const [lang, setL] = useState<Lang>(getLang());
  useEffect(() => subscribeLang(setL), []);
  return (
    <div data-no-tr role="radiogroup" aria-label="Language" style={{ display: "flex", gap: 8, margin: compact ? "10px 0 0" : "4px 0", justifyContent: "center" }}>
      {OPTIONS.map((o) => {
        const on = lang === o.id;
        return (
          <button
            key={o.id}
            type="button"
            role="radio"
            aria-checked={on}
            onClick={() => setLang(o.id)}
            style={{
              flex: 1, minWidth: 0, padding: compact ? "8px 6px" : "10px 6px", borderRadius: 12, cursor: "pointer", fontSize: 14, fontWeight: 700, lineHeight: 1.2,
              border: on ? "1.5px solid var(--iris)" : "1px solid var(--hair2, #DCDFE6)",
              background: on ? "var(--iris-tint, rgba(92,77,230,.10))" : "var(--surface, #fff)",
              color: on ? "var(--iris)" : "var(--muted, #586273)",
            }}
          >
            {o.label}
            {!compact && <div style={{ fontSize: 10.5, fontWeight: 600, opacity: 0.75, marginTop: 2 }}>{o.sub}</div>}
          </button>
        );
      })}
    </div>
  );
}
