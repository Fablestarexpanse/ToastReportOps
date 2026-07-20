"use client";

import { useCallback, useEffect, useState } from "react";
import {
  CredentialService,
  fetchCredentials,
  saveCredentials,
  testCredentials,
} from "@/lib/api";
import { C, font } from "@/lib/theme";
import { age, fmtStamp } from "@/lib/time";

/* API KEYS & SERVICES.

   Values are write-only from the browser's point of view: what you type is
   sent once and never comes back. The panel shows only whether a field is set,
   a masked hint (last 4), and whether the provider actually accepted it. */

const STATUS: Record<string, { color: string; label: string }> = {
  ok: { color: C.ok, label: "CONNECTED" },
  rejected: { color: C.evac, label: "REJECTED" },
  error: { color: C.warn, label: "UNREACHABLE" },
  unchecked: { color: C.dim, label: "NOT TESTED" },
  unconfigured: { color: C.faint, label: "NOT SET UP" },
};

export default function CredentialsPanel({ toast }: { toast: (m: string) => void }) {
  const [services, setServices] = useState<CredentialService[] | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<Record<string, string>>({});
  const [open, setOpen] = useState<Record<string, boolean>>({});

  const load = useCallback(async () => {
    try {
      setServices(await fetchCredentials());
    } catch {}
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const save = async (s: CredentialService) => {
    const updates = Object.fromEntries(
      s.fields.filter((f) => drafts[f.name] !== undefined).map((f) => [f.name, drafts[f.name]]),
    );
    if (!Object.keys(updates).length) return;
    setBusy((b) => ({ ...b, [s.id]: "save" }));
    try {
      setServices(await saveCredentials(updates));
      // drop the typed values from memory as soon as they're stored
      setDrafts((d) => {
        const next = { ...d };
        for (const k of Object.keys(updates)) delete next[k];
        return next;
      });
      toast(`${s.label.split(" — ")[0]} saved — testing…`);
      await test(s, true);
    } catch (e) {
      toast(`save failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy((b) => ({ ...b, [s.id]: "" }));
    }
  };

  const test = async (s: CredentialService, quiet = false) => {
    setBusy((b) => ({ ...b, [s.id]: "test" }));
    try {
      const r = await testCredentials(s.id);
      if (!quiet || r.status !== "ok") toast(`${s.id}: ${r.status}${r.message ? ` — ${r.message}` : ""}`);
      load();
    } catch (e) {
      toast(`test failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy((b) => ({ ...b, [s.id]: "" }));
    }
  };

  if (!services) return null;
  const connected = services.filter((s) => s.status === "ok").length;

  return (
    <div style={{ paddingTop: 4 }}>
      <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginBottom: 8 }}>
        keys are stored locally, never shown again after saving, and are stripped from backups ·{" "}
        {connected}/{services.length} connected
      </div>

      {services.map((s) => {
        const st = STATUS[s.status] ?? STATUS.unchecked;
        const isOpen = open[s.id];
        return (
          <div
            key={s.id}
            style={{
              border: `1px solid ${s.status === "ok" ? C.ok + "44" : C.line}`,
              borderRadius: 6,
              marginBottom: 7,
              background: C.bg,
            }}
          >
            <div
              onClick={() => setOpen((o) => ({ ...o, [s.id]: !o[s.id] }))}
              style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 10px", cursor: "pointer" }}
            >
              <span style={{ width: 7, height: 7, borderRadius: 4, background: st.color, flexShrink: 0 }} />
              <span style={{ fontSize: 11.5, fontWeight: 600 }}>{s.label}</span>
              <span style={{ marginLeft: "auto", fontFamily: font.mono, fontSize: 8, color: st.color, letterSpacing: 1 }}>
                {st.label}
              </span>
              <span style={{ fontFamily: font.mono, fontSize: 9, color: C.dim }}>{isOpen ? "▾" : "▸"}</span>
            </div>

            {!isOpen && s.message && (
              <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, padding: "0 10px 8px 25px" }}>
                {s.message}
                {s.checked_at && ` · ${age(s.checked_at)} ago`}
              </div>
            )}

            {isOpen && (
              <div style={{ padding: "0 10px 10px 10px" }}>
                <div style={{ fontSize: 10.5, color: C.dim, marginBottom: 7 }}>{s.why}</div>

                <a
                  href={s.signup_url}
                  target="_blank"
                  rel="noreferrer"
                  style={{
                    display: "inline-block",
                    fontFamily: font.mono,
                    fontSize: 9,
                    color: C.blue,
                    border: `1px solid ${C.blue}66`,
                    borderRadius: 4,
                    padding: "4px 9px",
                    textDecoration: "none",
                    marginBottom: 9,
                  }}
                >
                  ↗ {s.signup_label}
                </a>

                {s.notes && (
                  <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.warn, marginBottom: 8 }}>
                    ⚠ {s.notes}
                  </div>
                )}

                {s.fields.map((f) => (
                  <div key={f.name} style={{ marginBottom: 7 }}>
                    <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginBottom: 3 }}>
                      <span style={{ fontFamily: font.mono, fontSize: 9, color: C.dim }}>{f.label}</span>
                      {f.set && (
                        <span style={{ fontFamily: font.mono, fontSize: 8, color: C.ok }}>
                          {f.masked}
                        </span>
                      )}
                      {f.source === "env" && (
                        <span title="currently coming from .env" style={{ fontFamily: font.mono, fontSize: 7.5, color: C.faint, border: `1px solid ${C.line}`, borderRadius: 3, padding: "0 4px" }}>
                          .env
                        </span>
                      )}
                    </div>
                    <input
                      type={f.secret ? "password" : "text"}
                      autoComplete="off"
                      spellCheck={false}
                      value={drafts[f.name] ?? ""}
                      onChange={(e) => setDrafts((d) => ({ ...d, [f.name]: e.target.value }))}
                      placeholder={f.set ? "leave blank to keep current value" : f.placeholder || f.label}
                      style={{
                        width: "100%",
                        background: C.panel,
                        border: `1px solid ${C.line}`,
                        borderRadius: 4,
                        color: C.txt,
                        fontFamily: font.mono,
                        fontSize: 10.5,
                        padding: "6px 8px",
                        outline: "none",
                      }}
                    />
                  </div>
                ))}

                <div style={{ display: "flex", gap: 6, marginTop: 9, alignItems: "center" }}>
                  <button
                    onClick={() => save(s)}
                    disabled={busy[s.id] === "save"}
                    style={{ background: C.ok, color: C.bg, border: "none", borderRadius: 4, fontFamily: font.disp, fontWeight: 700, fontSize: 10.5, letterSpacing: 1, padding: "6px 12px", cursor: "pointer" }}
                  >
                    {busy[s.id] === "save" ? "SAVING…" : "SAVE & TEST"}
                  </button>
                  <button
                    onClick={() => test(s)}
                    disabled={busy[s.id] === "test" || !s.configured}
                    title={s.configured ? "make a read-only call to the provider" : "fill the fields first"}
                    style={{ background: "transparent", color: s.configured ? C.blue : C.faint, border: `1px solid ${s.configured ? C.blue : C.line}66`, borderRadius: 4, fontFamily: font.disp, fontSize: 10.5, letterSpacing: 1, padding: "6px 12px", cursor: s.configured ? "pointer" : "not-allowed" }}
                  >
                    {busy[s.id] === "test" ? "TESTING…" : "TEST"}
                  </button>
                  {s.checked_at && (
                    <span style={{ fontFamily: font.mono, fontSize: 8, color: C.faint, marginLeft: "auto" }}>
                      tested {fmtStamp(s.checked_at)}
                    </span>
                  )}
                </div>

                {s.message && (
                  <div style={{ fontFamily: font.mono, fontSize: 9, color: st.color, marginTop: 6 }}>
                    {s.message}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
