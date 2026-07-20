"use client";

import { useCallback, useEffect, useState } from "react";
import { EditablePrompt, fetchPrompts, resetPrompt, savePrompt } from "@/lib/api";
import { C, font } from "@/lib/theme";
import { fmtStamp } from "@/lib/time";

/* WRITING — the prompts that decide how the newsroom writes.

   The shipped version is always the fallback and can never be destroyed from
   here; REVERT deletes the override. Saves are validated: a prompt that has
   lost a {placeholder} still runs, it just silently stops receiving that data
   and the model fills the gap by invention — so that is refused. */

export default function PromptsPanel({ toast }: { toast: (m: string) => void }) {
  const [prompts, setPrompts] = useState<EditablePrompt[] | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string>("");

  const load = useCallback(async () => {
    try {
      setPrompts(await fetchPrompts());
    } catch {}
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const save = async (p: EditablePrompt) => {
    const template = drafts[p.id];
    if (template === undefined) return;
    setBusy(p.id);
    try {
      setPrompts(await savePrompt(p.id, template));
      setDrafts((d) => {
        const next = { ...d };
        delete next[p.id];
        return next;
      });
      toast(`${p.label} saved — next generation uses it`);
    } catch (e) {
      toast(`${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy("");
    }
  };

  const revert = async (p: EditablePrompt) => {
    setBusy(p.id);
    try {
      setPrompts(await resetPrompt(p.id));
      setDrafts((d) => {
        const next = { ...d };
        delete next[p.id];
        return next;
      });
      toast(`${p.label} reverted to the shipped prompt`);
    } catch (e) {
      toast(`revert failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy("");
    }
  };

  if (!prompts) return null;

  return (
    <div style={{ paddingTop: 4 }}>
      <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginBottom: 8 }}>
        edit how the AI writes · the shipped version is always kept and can be restored
      </div>

      {prompts.map((p) => {
        const isOpen = openId === p.id;
        const value = drafts[p.id] ?? p.template;
        const dirty = drafts[p.id] !== undefined && drafts[p.id] !== p.template;
        return (
          <div
            key={p.id}
            style={{
              border: `1px solid ${p.edited ? C.ember + "55" : C.line}`,
              borderRadius: 6,
              marginBottom: 7,
              background: C.bg,
            }}
          >
            <div
              onClick={() => setOpenId(isOpen ? null : p.id)}
              style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 10px", cursor: "pointer" }}
            >
              <span style={{ fontSize: 11.5, fontWeight: 600 }}>{p.label}</span>
              {p.edited && (
                <span style={{ fontFamily: font.mono, fontSize: 7.5, color: C.ember, border: `1px solid ${C.ember}66`, borderRadius: 3, padding: "0 5px" }}>
                  EDITED
                </span>
              )}
              <span style={{ marginLeft: "auto", fontFamily: font.mono, fontSize: 9, color: C.dim }}>
                {isOpen ? "▾" : "▸"}
              </span>
            </div>

            {!isOpen && (
              <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, padding: "0 10px 8px" }}>
                {p.purpose}
              </div>
            )}

            {isOpen && (
              <div style={{ padding: "0 10px 10px" }}>
                <div style={{ fontSize: 10.5, color: C.dim, marginBottom: 6 }}>{p.purpose}</div>

                <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.warn, marginBottom: 7 }}>
                  ⚠ keep every placeholder:{" "}
                  {p.placeholders.map((ph) => (
                    <span key={ph} style={{ color: C.ember }}>
                      {`{${ph}} `}
                    </span>
                  ))}
                  — they are where the real data is inserted. Keep the security
                  paragraph too: it is what stops ingested text giving the model orders.
                </div>

                <textarea
                  value={value}
                  onChange={(e) => setDrafts((d) => ({ ...d, [p.id]: e.target.value }))}
                  spellCheck={false}
                  style={{
                    width: "100%",
                    height: 260,
                    background: C.panel,
                    border: `1px solid ${dirty ? C.ember + "88" : C.line}`,
                    borderRadius: 5,
                    color: C.txt,
                    fontFamily: font.mono,
                    fontSize: 10,
                    lineHeight: 1.5,
                    padding: 9,
                    outline: "none",
                    resize: "vertical",
                  }}
                />

                <div style={{ display: "flex", gap: 6, marginTop: 8, alignItems: "center" }}>
                  <button
                    onClick={() => save(p)}
                    disabled={!dirty || busy === p.id}
                    style={{ background: dirty ? C.ok : C.faint, color: C.bg, border: "none", borderRadius: 4, fontFamily: font.disp, fontWeight: 700, fontSize: 10.5, letterSpacing: 1, padding: "6px 12px", cursor: dirty ? "pointer" : "not-allowed" }}
                  >
                    {busy === p.id ? "SAVING…" : "SAVE"}
                  </button>
                  {p.edited && (
                    <button
                      onClick={() => revert(p)}
                      style={{ background: "transparent", color: C.dim, border: `1px solid ${C.line}`, borderRadius: 4, fontFamily: font.disp, fontSize: 10.5, letterSpacing: 1, padding: "6px 12px", cursor: "pointer" }}
                    >
                      ↺ REVERT TO SHIPPED
                    </button>
                  )}
                  {dirty && (
                    <span style={{ fontFamily: font.mono, fontSize: 8.5, color: C.warn }}>unsaved</span>
                  )}
                  {p.updated_at && !dirty && (
                    <span style={{ marginLeft: "auto", fontFamily: font.mono, fontSize: 8, color: C.faint }}>
                      edited {fmtStamp(p.updated_at)}
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
