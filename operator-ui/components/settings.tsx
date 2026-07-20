"use client";

import { useCallback, useEffect, useState } from "react";
import {
  BackupStatus,
  SettingEntry,
  fetchBackups,
  fetchGnis,
  fetchSettings,
  loadGnis,
  runBackup,
  saveSettings,
} from "@/lib/api";
import CredentialsPanel from "@/components/credentials";
import { C, font } from "@/lib/theme";
import { TzMode, age, fmtStamp, setTzMode } from "@/lib/time";

/* OPTIONS — operator-adjustable runtime settings. Model choices live in the
   DB (they change pipeline behavior); display preferences are mirrored to
   localStorage so the UI renders correctly before the first fetch returns. */

export const LS_TZ = "toast-tz-mode";
export const LS_TIMELINE = "toast-timeline-order";

export function loadDisplayPrefs(): { tz: TzMode; timelineOrder: string } {
  if (typeof window === "undefined") return { tz: "utc", timelineOrder: "newest" };
  const tz = (localStorage.getItem(LS_TZ) as TzMode) || "utc";
  setTzMode(tz);
  return { tz, timelineOrder: localStorage.getItem(LS_TIMELINE) || "newest" };
}

/** Pull display prefs from the server (the source of truth) into localStorage,
    so a change made anywhere — or a revert to the .env default — reaches this
    browser. Returns true when something actually changed. */
export async function syncDisplayPrefs(): Promise<boolean> {
  try {
    const { settings } = await fetchSettings();
    const tz = (settings.timezone_display?.value as TzMode) || "utc";
    const order = settings.timeline_order?.value || "newest";
    const changed = tz !== localStorage.getItem(LS_TZ) || order !== localStorage.getItem(LS_TIMELINE);
    localStorage.setItem(LS_TZ, tz);
    localStorage.setItem(LS_TIMELINE, order);
    setTzMode(tz);
    return changed;
  } catch {
    return false;
  }
}

const CHOICES: Record<string, { value: string; label: string }[]> = {
  timezone_display: [
    { value: "utc", label: "ZULU / UTC" },
    { value: "local", label: "LOCAL" },
  ],
  timeline_order: [
    { value: "newest", label: "NEWEST FIRST" },
    { value: "oldest", label: "OLDEST FIRST" },
  ],
};

/* GNIS gazetteer — public domain, no key. Without it the attribution scorer's
   geo term is dead weight on every media fact. */
function GnisSection({ toast }: { toast: (m: string) => void }) {
  const [features, setFeatures] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setFeatures((await fetchGnis()).features);
    } catch {}
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const run = async () => {
    setBusy(true);
    try {
      await loadGnis();
      toast("downloading place names — this takes about a minute");
      setTimeout(load, 45000);
    } catch (e) {
      toast(`load failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy(false);
    }
  };

  if (features === null) return null;
  return (
    <div style={{ padding: "9px 0", borderBottom: `1px solid ${C.line}44` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ width: 7, height: 7, borderRadius: 4, background: features ? C.ok : C.warn }} />
        <span style={{ fontFamily: font.mono, fontSize: 9.5, color: C.dim }}>
          {features
            ? `${features.toLocaleString()} California place names loaded`
            : "not loaded — place names cannot be geocoded"}
        </span>
        <button
          onClick={run}
          disabled={busy}
          style={{ marginLeft: "auto", background: features ? "transparent" : C.ok, color: features ? C.blue : C.bg, border: `1px solid ${features ? C.blue + "66" : "transparent"}`, borderRadius: 4, fontFamily: font.disp, fontWeight: 700, fontSize: 10.5, letterSpacing: 1, padding: "4px 10px", cursor: "pointer", whiteSpace: "nowrap" }}
        >
          {busy ? "LOADING…" : features ? "RELOAD" : "LOAD NOW"}
        </button>
      </div>
      <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginTop: 4 }}>
        USGS gazetteer (public domain, no key) · turns “near Loyalton” into coordinates and places
        newly-named fires · works offline once loaded
      </div>
    </div>
  );
}

function BackupSection({ toast }: { toast: (m: string) => void }) {
  const [status, setStatus] = useState<BackupStatus | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setStatus(await fetchBackups());
    } catch {}
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const run = async () => {
    setBusy(true);
    try {
      const r = await runBackup();
      toast(`snapshot verified: ${r.manifest.file}`);
      load();
    } catch (e) {
      toast(`backup failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy(false);
    }
  };

  if (!status) return null;
  const facts = status.latest?.verified?.counts?.facts;
  return (
    <div style={{ padding: "9px 0", borderBottom: `1px solid ${C.line}44` }}>
      {status.at_risk && (
        <div
          style={{
            border: `1px solid ${C.evac}66`,
            background: C.evac + "14",
            borderRadius: 5,
            padding: "6px 9px",
            marginBottom: 7,
            fontFamily: font.mono,
            fontSize: 9,
            color: C.evac,
          }}
        >
          ⚠ NO OFFSITE COPY — snapshots live on this machine only. Add B2 keys to
          .env (docs/BACKUP-RESTORE.md); a house fire takes the archive with it.
        </div>
      )}
      <div style={{ fontFamily: font.mono, fontSize: 9.5, color: C.dim, display: "flex", gap: 8, alignItems: "center" }}>
        <span>
          {status.snapshots} snapshot{status.snapshots === 1 ? "" : "s"}
          {status.latest_at && (
            <>
              {" · last "}
              <span style={{ color: C.txt }}>{fmtStamp(status.latest_at)}</span>
              {` (${age(status.latest_at)} ago)`}
            </>
          )}
          {facts != null && ` · ${facts.toLocaleString()} facts verified`}
        </span>
        <button
          onClick={run}
          disabled={busy}
          style={{ marginLeft: "auto", background: busy ? C.faint : C.blue, color: C.bg, border: "none", borderRadius: 4, fontFamily: font.disp, fontWeight: 700, fontSize: 10.5, letterSpacing: 1, padding: "4px 10px", cursor: "pointer", whiteSpace: "nowrap" }}
        >
          {busy ? "RUNNING…" : "BACK UP NOW"}
        </button>
      </div>
      <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginTop: 4 }}>
        nightly, automatic · every snapshot is integrity-checked and row-counted before it counts as good
      </div>
    </div>
  );
}

export default function SettingsPanel({
  onClose,
  onApplied,
  toast,
}: {
  onClose: () => void;
  onApplied: () => void;
  toast: (m: string) => void;
}) {
  const [settings, setSettings] = useState<Record<string, SettingEntry> | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await fetchSettings();
      setSettings(d.settings);
      setModels(d.models);
      setDraft(Object.fromEntries(Object.entries(d.settings).map(([k, v]) => [k, v.value])));
    } catch (e) {
      toast(`settings unavailable: ${e instanceof Error ? e.message : e}`);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  const set = (k: string, v: string) => setDraft((d) => ({ ...d, [k]: v }));

  const save = async () => {
    setBusy(true);
    try {
      const d = await saveSettings(draft);
      setSettings(d.settings);
      // display prefs take effect immediately, before any refetch
      const tz = (d.settings.timezone_display?.value as TzMode) || "utc";
      localStorage.setItem(LS_TZ, tz);
      setTzMode(tz);
      localStorage.setItem(LS_TIMELINE, d.settings.timeline_order?.value || "newest");
      toast("settings saved");
      onApplied();
      onClose();
    } catch (e) {
      toast(`save failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy(false);
    }
  };

  const row = (key: string, children: React.ReactNode, note?: string) => {
    const s = settings?.[key];
    if (!s) return null;
    return (
      <div key={key} style={{ padding: "9px 0", borderBottom: `1px solid ${C.line}44` }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 5 }}>
          <span style={{ fontFamily: font.mono, fontSize: 9.5, color: C.dim, letterSpacing: 0.5 }}>
            {s.label.toUpperCase()}
          </span>
          {s.overridden && (
            <span
              onClick={() => set(key, "")}
              title={`click to revert to the .env default (${s.default})`}
              style={{ fontFamily: font.mono, fontSize: 8, color: C.warn, border: `1px solid ${C.warn}55`, borderRadius: 3, padding: "0 5px", cursor: "pointer" }}
            >
              OVERRIDDEN · REVERT
            </span>
          )}
        </div>
        {children}
        {note && (
          <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginTop: 4 }}>{note}</div>
        )}
      </div>
    );
  };

  const chips = (key: string) => (
    <div style={{ display: "flex", gap: 6 }}>
      {CHOICES[key].map((c) => (
        <span
          key={c.value}
          onClick={() => set(key, c.value)}
          style={{
            fontFamily: font.mono,
            fontSize: 9.5,
            padding: "4px 10px",
            borderRadius: 4,
            cursor: "pointer",
            border: `1px solid ${draft[key] === c.value ? C.ember : C.line}`,
            color: draft[key] === c.value ? C.ember : C.dim,
            background: draft[key] === c.value ? C.ember + "18" : "transparent",
          }}
        >
          {c.label}
        </span>
      ))}
    </div>
  );

  // Ollama reports "name:latest"; a bare "name" is the same model.
  const matchModel = (want: string) =>
    models.find((m) => m === want || m === `${want}:latest` || m.replace(/:latest$/, "") === want);

  const modelField = (key: string) => {
    const matched = matchModel(draft[key] ?? "");
    return (
      <>
      {models.length ? (
        <select
          value={matched ?? ""}
          onChange={(e) => set(key, e.target.value)}
          style={{ width: "100%", background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.txt, fontFamily: font.mono, fontSize: 11, padding: "6px 8px", outline: "none" }}
        >
          {!matched && <option value="">{draft[key]} (not pulled)</option>}
          {models.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      ) : (
        <input
          value={draft[key] ?? ""}
          onChange={(e) => set(key, e.target.value)}
          style={{ width: "100%", background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.txt, fontFamily: font.mono, fontSize: 11, padding: "6px 8px", outline: "none" }}
        />
      )}
      </>
    );
  };

  return (
    <div
      onClick={onClose}
      style={{ position: "fixed", inset: 0, background: "#000a", zIndex: 200, display: "flex", alignItems: "flex-start", justifyContent: "center", paddingTop: 70 }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{ width: 460, maxHeight: "80vh", overflowY: "auto", background: C.panelUp, border: `1px solid ${C.line}`, borderRadius: 9, boxShadow: "0 20px 60px #000c", padding: "14px 16px" }}
      >
        <div style={{ display: "flex", alignItems: "baseline", marginBottom: 4 }}>
          <span style={{ fontFamily: font.disp, fontSize: 18, fontWeight: 700, letterSpacing: 1.5 }}>
            OPTIONS
          </span>
          <span onClick={onClose} style={{ marginLeft: "auto", cursor: "pointer", color: C.dim, fontFamily: font.mono, fontSize: 12 }}>
            ✕
          </span>
        </div>
        {!settings ? (
          <div style={{ fontFamily: font.mono, fontSize: 10, color: C.faint, padding: 12 }}>loading…</div>
        ) : (
          <>
            <div style={{ fontFamily: font.disp, fontSize: 11, letterSpacing: 2, color: C.ember, marginTop: 10 }}>
              DISPLAY
            </div>
            {row("timezone_display", chips("timezone_display"), "all times shown 24-hour with the date (e.g. 20JUL 0137Z)")}
            {row("timeline_order", chips("timeline_order"), "default order for the incident TIMELINE tab")}
            {row(
              "ui_poll_interval_s",
              <input
                type="number"
                min={3}
                max={120}
                value={draft.ui_poll_interval_s ?? ""}
                onChange={(e) => set("ui_poll_interval_s", e.target.value)}
                style={{ width: 90, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.txt, fontFamily: font.mono, fontSize: 11, padding: "6px 8px", outline: "none" }}
              />,
              "how often the workstation refetches live data",
            )}

            <div style={{ fontFamily: font.disp, fontSize: 11, letterSpacing: 2, color: C.ember, marginTop: 14 }}>
              LOCAL AI
            </div>
            {row("llm_model", modelField("llm_model"), "used for extraction, drafting, social rewrites and deep research")}
            {row("embedding_model", modelField("embedding_model"), "corroboration matching — changing this invalidates existing embeddings")}
            {!models.length && (
              <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.warn, marginTop: 6 }}>
                ⚠ model list unavailable — is Ollama running? free-text entry still works.
              </div>
            )}

            <div style={{ fontFamily: font.disp, fontSize: 11, letterSpacing: 2, color: C.ember, marginTop: 16 }}>
              API KEYS &amp; SERVICES
            </div>
            <CredentialsPanel toast={toast} />

            <div style={{ fontFamily: font.disp, fontSize: 11, letterSpacing: 2, color: C.ember, marginTop: 14 }}>
              PLACE NAMES (GNIS)
            </div>
            <GnisSection toast={toast} />

            <div style={{ fontFamily: font.disp, fontSize: 11, letterSpacing: 2, color: C.ember, marginTop: 14 }}>
              BACKUPS
            </div>
            <BackupSection toast={toast} />

            <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
              <button
                onClick={save}
                disabled={busy}
                style={{ flex: 1, background: busy ? C.faint : C.ok, color: C.bg, border: "none", borderRadius: 5, fontFamily: font.disp, fontWeight: 700, fontSize: 12, letterSpacing: 1, padding: "8px 0", cursor: "pointer" }}
              >
                {busy ? "SAVING…" : "SAVE"}
              </button>
              <button
                onClick={onClose}
                style={{ background: "transparent", color: C.dim, border: `1px solid ${C.line}`, borderRadius: 5, fontFamily: font.disp, fontSize: 12, letterSpacing: 1, padding: "8px 16px", cursor: "pointer" }}
              >
                CANCEL
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
