"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Draft,
  DraftDetail,
  Incident,
  ResearchJob,
  cancelResearch,
  createSocialDraft,
  fetchDraft,
  buildSiteNow,
  fetchCredStatuses,
  fetchPublishChannels,
  makeGraphic,
  releaseDraft,
  renderMarkdown,
  retryPublication,
  archiveDraft,
  deleteDraft,
  fetchDrafts,
  fetchIncidents,
  generateDraft,
  generateStatewide,
  saveDraft,
  transitionDraft,
} from "@/lib/api";
import { C, TIER, font } from "@/lib/theme";
import { age, fmtStamp } from "@/lib/time";

const statusColor = (s: string) =>
  s.startsWith("published") ? C.ok : s === "needs_review" ? C.warn : s === "approved" ? C.blue : s === "rejected" ? C.evac : C.dim;

const KIND_BADGE: Record<string, [string, string]> = {
  update: ["UPDATE", C.ember],
  analysis: ["RESEARCH", C.violet],
  social: ["SOCIAL", C.blue],
  summary: ["SUMMARY", C.dim],
  correction: ["CORRECTION", C.evac],
};

const WINDOWS: [string, number | null][] = [
  ["Full incident", null],
  ["Last 24h", 24],
  ["Last 3h", 3],
  ["Last hour", 1],
];

export default function NewsroomMode({ toast }: { toast: (m: string) => void }) {
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [research, setResearch] = useState<ResearchJob[]>([]);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [selId, setSelId] = useState<string | null>(null);
  const [detail, setDetail] = useState<DraftDetail | null>(null);
  const [genIncident, setGenIncident] = useState<string | null>(null);
  const [genWindow, setGenWindow] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [socialBusy, setSocialBusy] = useState(false);
  const [bodyEdit, setBodyEdit] = useState("");
  const [queueFilter, setQueueFilter] = useState<
    "all" | "update" | "social" | "research" | "published" | "archived"
  >("all");
  const [archivedDrafts, setArchivedDrafts] = useState<Draft[]>([]);
  const [titleEdit, setTitleEdit] = useState("");

  const refresh = useCallback(async () => {
    // A failed poll must never take the screen away. Before this, one network
    // blip threw out of the effect and Next replaced the whole workstation with
    // an error page — on a tool whose operating condition is PSPS outages and
    // flaky links. Keep whatever we last had; the top bar says it is stale.
    let d, inc;
    try {
      [d, inc] = await Promise.all([fetchDrafts(), fetchIncidents()]);
    } catch {
      return;
    }
    // defensively dedupe by id — a transient duplicate from overlapping
    // fetches must never produce duplicate React keys. The incidents list
    // learned this first; the drafts list re-taught it (operator hit the
    // duplicate-key crash on a just-restored draft).
    const seen = new Set<string>();
    const unique = inc.filter((i) => !seen.has(i.id) && seen.add(i.id) !== undefined);
    const seenD = new Set<string>();
    setDrafts(d.drafts.filter((x) => !seenD.has(x.id) && seenD.add(x.id) !== undefined));
    setResearch(d.research_jobs ?? []);
    setIncidents(unique);
    // functional update: no dependency on genIncident, so refresh keeps a
    // stable identity and the mount effect runs exactly once. null means
    // "not chosen yet" and defaults to the first fire; "" is the operator's
    // DELIBERATE "All fires" choice and must never be clobbered by a poll —
    // it was, every 8 seconds, until "" and null were told apart.
    setGenIncident((prev) => (prev === null && unique.length ? unique[0].id : prev));
  }, []);

  useEffect(() => {
    refresh();
    // keep the queue live: in-flight research entries update, and finished
    // research pops in as a draft without a manual reload
    const id = setInterval(refresh, 8000);
    return () => clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    if (queueFilter !== "archived") return;
    let live = true;
    fetchDrafts(true)
      .then((r) => {
        if (!live) return;
        const seen = new Set<string>();
        setArchivedDrafts(r.drafts.filter((x) => !seen.has(x.id) && seen.add(x.id) !== undefined));
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [queueFilter, drafts.length]);

  const doArchive = async (id: string, archived: boolean) => {
    try {
      await archiveDraft(id, archived);
      toast(archived ? "sent to archive" : "restored to queue");
      if (selId === id && archived) setSelId(null);
      refresh();
      if (queueFilter === "archived") {
        const r = await fetchDrafts(true);
        setArchivedDrafts(r.drafts);
      }
    } catch (e) {
      toast(`${e instanceof Error ? e.message : e}`);
    }
  };

  const doDelete = async (id: string) => {
    try {
      await deleteDraft(id);
      toast("draft deleted");
      if (selId === id) setSelId(null);
      refresh();
    } catch (e) {
      toast(`${e instanceof Error ? e.message : e}`); // published/delivered → "archive instead"
    }
  };

  const renderedIds = new Set<string>();
  const visibleDrafts = (queueFilter === "archived" ? archivedDrafts : drafts).filter((dr) => {
    if (renderedIds.has(dr.id)) return false; // belt AND suspenders at the render boundary
    renderedIds.add(dr.id);
    // the incident selector doubles as the queue's fire filter — the queue
    // follows the fire you are working; "All fires" shows everything
    if (genIncident && dr.incident_id !== genIncident) return false;
    if (queueFilter === "all" || queueFilter === "archived") return true;
    if (queueFilter === "published") return dr.status === "published";
    if (queueFilter === "research") return dr.kind === "analysis";
    return dr.kind === queueFilter;
  });

  const visibleResearch = research.filter(
    (r) => !genIncident || r.incident_id === genIncident,
  );

  const fireName = genIncident
    ? incidents.find((i) => i.id === genIncident)?.canonical_name
    : null;

  useEffect(() => {
    if (!selId) {
      setDetail(null);
      return;
    }
    fetchDraft(selId).then((d) => {
      setDetail(d);
      setBodyEdit(d.draft.body_md);
      setTitleEdit(d.draft.title ?? "");
      setPreviewMode(false);
    });
  }, [selId]);

  const generate = async () => {
    if (busy) return;
    setBusy(true);
    try {
      if (!genIncident) {
        // "All fires" selected: the statewide roundup — mechanical, cited
        const r = await generateStatewide();
        toast(`statewide roundup — ${r.fires} fires, ${r.under_evac} under evac, ${r.cited} cited`);
        await refresh();
        setSelId(r.draft_id);
      } else {
        const w: { window_start?: string } = {};
        if (genWindow) w.window_start = new Date(Date.now() - genWindow * 3600_000).toISOString();
        const r = await generateDraft(genIncident, w);
        toast(`draft generated — ${r.facts_used} facts, ${r.cited} cited`);
        await refresh();
        setSelId(r.draft_id);
      }
    } catch (e) {
      toast(`draft failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!detail) return;
    await saveDraft(detail.draft.id, { body_md: bodyEdit, title: titleEdit || undefined });
    toast("saved");
    refresh();
  };

  const copyBody = async () => {
    await navigator.clipboard.writeText(bodyEdit);
    toast("copied to clipboard");
  };

  const makeSocial = async () => {
    if (!detail || socialBusy) return;
    setSocialBusy(true);
    try {
      const r = await createSocialDraft(detail.draft.id);
      toast(`social version drafted (${r.post_chars} chars + ${r.short_chars}-char short)`);
      await refresh();
      setSelId(r.draft_id);
    } catch (e) {
      toast(`social rewrite failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setSocialBusy(false);
    }
  };

  // channel-selected release: which channels have working credentials
  const [channels, setChannels] = useState<string[]>([]);
  const [selChannels, setSelChannels] = useState<Set<string>>(new Set());
  const [schedAt, setSchedAt] = useState("");
  const [releasing, setReleasing] = useState(false);
  const [credStatus, setCredStatus] = useState<Record<string, string>>({});
  useEffect(() => {
    fetchPublishChannels()
      .then((r) => {
        setChannels(r.channels);
        setSelChannels(new Set(r.channels)); // default: everything configured
      })
      .catch(() => undefined);
    fetchCredStatuses().then(setCredStatus).catch(() => undefined);
  }, []);

  const doRelease = async () => {
    if (!detail) return;
    const picked = [...selChannels];
    if (!picked.length) {
      toast("select at least one channel");
      return;
    }
    setReleasing(true);
    try {
      const at = schedAt ? new Date(schedAt).toISOString() : undefined;
      const r = await releaseDraft(detail.draft.id, picked, at);
      toast(
        r.scheduled_for
          ? `scheduled for ${schedAt} → ${picked.join(", ")}`
          : `released now → ${picked.join(", ")}`,
      );
      setSchedAt("");
      const d2 = await fetchDraft(detail.draft.id);
      setDetail(d2);
      refresh();
    } catch (e) {
      toast(`release failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setReleasing(false);
    }
  };

  const [previewMode, setPreviewMode] = useState(false);
  const [previewHtml, setPreviewHtml] = useState("");
  const togglePreview = async () => {
    if (previewMode) {
      setPreviewMode(false);
      return;
    }
    try {
      const r = await renderMarkdown(bodyEdit);
      setPreviewHtml(r.html);
      setPreviewMode(true);
    } catch (e) {
      toast(`preview failed: ${e instanceof Error ? e.message : e}`);
    }
  };

  const [graphicBusy, setGraphicBusy] = useState(false);
  const [graphicNonce, setGraphicNonce] = useState(0);
  const doGraphic = async () => {
    if (!detail) return;
    setGraphicBusy(true);
    try {
      const r = await makeGraphic(detail.draft.id);
      toast(`card rendered (${Math.round(r.bytes / 1024)} KB) — attaches to Bluesky posts`);
      setGraphicNonce((n) => n + 1);
      setDetail(await fetchDraft(detail.draft.id));
    } catch (e) {
      toast(`graphic failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setGraphicBusy(false);
    }
  };

  const doRetry = async (pubId: string) => {
    try {
      const r = await retryPublication(pubId);
      toast(`re-sending to ${r.channel}`);
      if (detail) setDetail(await fetchDraft(detail.draft.id));
    } catch (e) {
      toast(`${e instanceof Error ? e.message : e}`);
    }
  };

  const advance = async (status: string) => {
    if (!detail) return;
    try {
      await transitionDraft(detail.draft.id, status);
      toast(`draft → ${status}`);
      const d = await fetchDraft(detail.draft.id);
      setDetail(d);
      refresh();
    } catch (e) {
      toast(`${e instanceof Error ? e.message : e}`);
    }
  };

  const download = (name: string, text: string, mime = "text/plain") => {
    const url = URL.createObjectURL(new Blob([text], { type: mime }));
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  };

  const pushOne = async (channel: string) => {
    if (!detail) return;
    try {
      const r = await releaseDraft(detail.draft.id, [channel]);
      toast(r.created.length ? `pushed → ${channel}` : `${channel} already has this post`);
      setDetail(await fetchDraft(detail.draft.id));
    } catch (e) {
      toast(`${e instanceof Error ? e.message : e}`);
    }
  };

  const doBuildSite = async () => {
    try {
      await buildSiteNow();
      toast("site build queued — ARCHIVE → publishes to your local site output");
    } catch (e) {
      toast(`site build failed: ${e instanceof Error ? e.message : e}`);
    }
  };

  const d = detail?.draft;
  const nextActions: [string, string, string][] = d
    ? d.status === "draft"
      ? [["needs_review", "SUBMIT FOR REVIEW", C.warn]]
      : d.status === "needs_review"
        ? [["approved", "APPROVE ✓", C.ok], ["draft", "SEND BACK", C.dim]]
        : d.status === "approved" && d.kind !== "social"
          ? [["published", "MARK RELEASED ✓", C.ok]]
          : []
    : [];

  return (
    <div style={{ flex: 1, display: "grid", gridTemplateColumns: "300px 1fr 280px", minHeight: 0 }}>
      {/* draft queue + generate */}
      <div style={{ borderRight: `1px solid ${C.line}`, background: C.panel, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ padding: 10, borderBottom: `1px solid ${C.line}` }}>
          <div style={{ fontFamily: font.disp, fontSize: 12, letterSpacing: 2, color: C.dim, marginBottom: 6 }}>
            GENERATE UPDATE
          </div>
          <select
            value={genIncident ?? ""}
            onChange={(e) => setGenIncident(e.target.value)}
            style={{ width: "100%", background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: C.txt, fontFamily: font.mono, fontSize: 10.5, padding: "6px 8px", marginBottom: 6 }}
          >
            <option value="">All fires — full queue</option>
            {incidents.map((i) => (
              <option key={i.id} value={i.id}>
                {i.canonical_name} · {i.fact_count} facts
              </option>
            ))}
          </select>
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 6 }}>
            {WINDOWS.map(([label, hrs]) => (
              <span
                key={label}
                onClick={() => setGenWindow(hrs)}
                style={{ fontFamily: font.mono, fontSize: 8.5, padding: "2px 7px", borderRadius: 3, cursor: "pointer", border: `1px solid ${genWindow === hrs ? C.ember : C.line}`, color: genWindow === hrs ? C.ember : C.dim }}
              >
                {label}
              </span>
            ))}
          </div>
          <button onClick={generate} style={{ width: "100%", background: busy ? C.faint : C.ember, color: C.bg, border: "none", borderRadius: 5, fontFamily: font.disp, fontWeight: 700, fontSize: 12, letterSpacing: 1, padding: "7px 0", cursor: "pointer" }}>
            {busy ? "drafting…" : genIncident ? "✎ DRAFT FROM LEDGER" : "✎ STATEWIDE ROUNDUP"}
          </button>
        </div>
        <div style={{ padding: "8px 12px 4px", fontFamily: font.disp, fontSize: 12, letterSpacing: 2, color: C.dim }}>
          DRAFT QUEUE
          {fireName && (
            <span style={{ color: C.ember, letterSpacing: 0.5, fontSize: 10 }}> · {fireName}</span>
          )}
        </div>
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap", padding: "0 12px 6px" }}>
          {([
            ["all", "ALL"],
            ["update", "UPDATE"],
            ["social", "SOCIAL"],
            ["research", "RESEARCH"],
            ["published", "PUBLISHED"],
            ["archived", "ARCHIVED"],
          ] as const).map(([k, label]) => (
            <span
              key={k}
              onClick={() => setQueueFilter(k)}
              style={{ fontFamily: font.mono, fontSize: 8, padding: "2px 7px", borderRadius: 3, cursor: "pointer", letterSpacing: 0.5, border: `1px solid ${queueFilter === k ? C.ember : C.line}`, color: queueFilter === k ? C.ember : C.dim }}
            >
              {label}
            </span>
          ))}
        </div>
        <div style={{ flex: 1, overflowY: "auto" }}>
          {visibleResearch.map((r) => {
            const cfg =
              r.state === "failed"
                ? { color: C.evac, icon: "✕", label: "RESEARCH FAILED" }
                : r.state === "parked"
                  ? { color: C.warn, icon: "⏸", label: "RESEARCH PARKED — rate limited, retrying" }
                  : { color: C.violet, icon: "⌕", label: r.state === "starting" ? "RESEARCH STARTING" : "RESEARCHING…" };
            return (
              <div
                key={r.key}
                style={{
                  padding: "9px 12px",
                  borderLeft: `3px solid ${cfg.color}`,
                  background: cfg.color + "0d",
                  borderBottom: `1px solid ${C.line}55`,
                }}
                title={r.error ?? r.query}
              >
                <div style={{ display: "flex", gap: 7, alignItems: "center" }}>
                  <span
                    className={r.state === "failed" ? undefined : "tr-blink"}
                    style={{ color: cfg.color, fontSize: 13 }}
                  >
                    {cfg.icon}
                  </span>
                  <span style={{ fontSize: 11.5, fontWeight: 600, lineHeight: 1.3, minWidth: 0, flex: 1 }}>
                    {r.incident_name ? `${r.incident_name} — deep research` : r.query.slice(0, 60)}
                  </span>
                  <span
                    onClick={async (e) => {
                      e.stopPropagation();
                      try {
                        await cancelResearch(r.key);
                        toast(r.state === "failed" ? "dismissed" : "research cancelled");
                        refresh();
                      } catch (err) {
                        toast(`cancel failed: ${err instanceof Error ? err.message : err}`);
                      }
                    }}
                    title={r.state === "failed" ? "dismiss" : "cancel this research run"}
                    style={{ color: C.faint, cursor: "pointer", fontSize: 12, padding: "0 3px" }}
                  >
                    ✕
                  </span>
                </div>
                <div style={{ fontFamily: font.mono, fontSize: 8.5, color: cfg.color, marginTop: 3 }}>
                  {cfg.label}
                  <span style={{ color: C.faint }}>
                    {" "}
                    · running {age(r.since)} · since {fmtStamp(r.since)} · lands here when done
                  </span>
                </div>
                {r.error && (
                  <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginTop: 2 }}>
                    {r.error}
                  </div>
                )}
              </div>
            );
          })}
          {visibleDrafts.length === 0 && visibleResearch.length === 0 && (
            <div style={{ padding: "14px 12px", fontFamily: font.mono, fontSize: 9.5, color: C.faint, lineHeight: 1.6 }}>
              {fireName
                ? `no drafts for the ${fireName} yet — generate one above, or switch to "All fires"`
                : queueFilter === "archived"
                  ? "nothing archived"
                  : "no drafts match this filter"}
            </div>
          )}
          {visibleDrafts.map((dr) => (
            <div
              key={dr.id}
              onClick={() => setSelId(dr.id)}
              className="tr-row"
              style={{ position: "relative", padding: "9px 12px", borderLeft: `3px solid ${dr.id === selId ? C.ember : "transparent"}`, background: dr.id === selId ? C.panelUp : undefined, cursor: "pointer", borderBottom: `1px solid ${C.line}55` }}
            >
              <span style={{ position: "absolute", top: 7, right: 9, display: "flex", gap: 6 }}>
                <span
                  onClick={(e) => {
                    e.stopPropagation();
                    doArchive(dr.id, queueFilter !== "archived");
                  }}
                  title={queueFilter === "archived" ? "restore to the queue" : "send to archive (nothing is lost — the ARCHIVED filter shows it)"}
                  style={{ fontFamily: font.mono, fontSize: 9, color: C.faint, cursor: "pointer" }}
                >
                  {queueFilter === "archived" ? "↩" : "⌸"}
                </span>
                {dr.status !== "published" && queueFilter !== "archived" && (
                  <span
                    onClick={(e) => {
                      e.stopPropagation();
                      doDelete(dr.id);
                    }}
                    title="delete (only drafts that never published and never attempted delivery)"
                    style={{ fontFamily: font.mono, fontSize: 9, color: C.faint, cursor: "pointer" }}
                  >
                    ✕
                  </span>
                )}
              </span>
              <div style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
                <span
                  style={{
                    fontFamily: font.mono,
                    fontSize: 7.5,
                    fontWeight: 700,
                    letterSpacing: 0.5,
                    color: (KIND_BADGE[dr.kind] ?? KIND_BADGE.update)[1],
                    border: `1px solid ${(KIND_BADGE[dr.kind] ?? KIND_BADGE.update)[1]}55`,
                    borderRadius: 3,
                    padding: "1px 4px",
                    flexShrink: 0,
                  }}
                >
                  {(KIND_BADGE[dr.kind] ?? KIND_BADGE.update)[0]}
                </span>
                <span style={{ fontSize: 12, fontWeight: 600, lineHeight: 1.3, minWidth: 0 }}>
                  {dr.title || "untitled"}
                </span>
              </div>
              <div style={{ fontFamily: font.mono, fontSize: 9, marginTop: 3, display: "flex", gap: 8 }}>
                <span style={{ color: statusColor(dr.status) }}>{dr.status.toUpperCase().replace("_", " ")}</span>
                <span style={{ color: C.faint }}>{dr.incident_name}</span>
                <span style={{ color: C.faint, marginLeft: "auto" }}>{dr.cited_count} cited</span>
              </div>
            </div>
          ))}
          {!drafts.length && !research.length && (
            <div style={{ padding: 14, fontFamily: font.mono, fontSize: 10, color: C.faint }}>
              no drafts yet — generate one above
            </div>
          )}
        </div>
      </div>

      {/* editor */}
      <div style={{ display: "flex", flexDirection: "column", minWidth: 0, minHeight: 0, background: C.bg }}>
        {!d && (
          <div style={{ padding: 24, fontFamily: font.mono, fontSize: 11, color: C.faint }}>
            select a draft, or generate one from an incident&apos;s ledger.
          </div>
        )}
        {d && (
          <>
            <div style={{ padding: "10px 16px", borderBottom: `1px solid ${C.line}`, display: "flex", alignItems: "center", gap: 10 }}>
              <input
                value={titleEdit}
                onChange={(e) => setTitleEdit(e.target.value)}
                readOnly={d.status === "published"}
                style={{ fontFamily: font.disp, fontSize: 16, fontWeight: 700, flex: 1, minWidth: 0, background: "transparent", border: "none", outline: "none", color: C.txt, borderBottom: `1px dashed transparent` }}
                onFocus={(e) => (e.target.style.borderBottom = `1px dashed ${C.line}`)}
                onBlur={(e) => (e.target.style.borderBottom = "1px dashed transparent")}
              />
              <span style={{ fontFamily: font.mono, fontSize: 9, color: statusColor(d.status), border: `1px solid ${statusColor(d.status)}`, borderRadius: 3, padding: "2px 7px", flexShrink: 0 }}>
                {d.status.toUpperCase().replace("_", " ")}
              </span>
            </div>
            <div style={{ flex: 1, overflowY: "auto", padding: "14px 18px" }}>
              {(() => {
                const stale = detail?.cited_facts.filter((f) => f.superseded_by) ?? [];
                if (!stale.length) return null;
                const published = d.status === "published";
                return (
                  /* the doctrine guard: a draft must not be approved on numbers
                     the ledger has already replaced */
                  <div style={{ border: `1px solid ${C.evac}`, background: C.evac + "14", borderRadius: 7, padding: "9px 12px", marginBottom: 12 }}>
                    <div style={{ fontFamily: font.disp, fontSize: 12, fontWeight: 700, letterSpacing: 1, color: C.evac }}>
                      ⚠ {stale.length} CITED {stale.length === 1 ? "FACT HAS" : "FACTS HAVE"} BEEN SUPERSEDED
                      {published ? " SINCE PUBLICATION" : " — THE NUMBERS MAY BE STALE"}
                    </div>
                    {stale.slice(0, 4).map((f) => (
                      <div key={f.id} style={{ fontFamily: font.mono, fontSize: 8.5, color: C.dim, marginTop: 4 }}>
                        <span style={{ textDecoration: "line-through", color: C.faint }}>{f.content.slice(0, 70)}</span>
                        {f.superseded_content && (
                          <span style={{ color: C.txt }}> → {f.superseded_content.slice(0, 70)}</span>
                        )}
                      </div>
                    ))}
                    <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.evac, marginTop: 5 }}>
                      {published
                        ? "consider a follow-up update with the current figures"
                        : "generate a fresh draft from the ledger before approving"}
                    </div>
                  </div>
                );
              })()}
              {detail?.draft.graphic_sha && (
                /* the attached card as the article's hero — how readers will
                   actually see the post, not a thumbnail in a side rail */
                <div style={{ marginBottom: 14 }}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={`/api/drafts/${detail.draft.id}/graphic.png?t=${graphicNonce}`}
                    alt="attached incident card"
                    style={{ width: "100%", maxWidth: 760, borderRadius: 8, border: `1px solid ${C.line}`, display: "block" }}
                  />
                  <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 4 }}>
                    <a
                      href={`/api/drafts/${detail.draft.id}/graphic.png`}
                      download={`${(detail.draft.title || detail.draft.id).slice(0, 40).replace(/[^\w -]/g, "")}-card.png`}
                      style={{ fontFamily: font.mono, fontSize: 9.5, color: C.blue, border: `1px solid ${C.blue}55`, borderRadius: 4, padding: "3px 10px", textDecoration: "none", whiteSpace: "nowrap" }}
                    >
                      ⤓ SAVE GRAPHIC
                    </a>
                    <span style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint }}>
                      attached card — publishes with the post on image-capable channels ·
                      regenerate with 🗺 GRAPHIC after new numbers land
                    </span>
                  </div>
                </div>
              )}
              <div style={{ display: "flex", gap: 4, marginBottom: 8 }}>
                {(["EDIT", "PREVIEW"] as const).map((m) => {
                  const active = (m === "PREVIEW") === previewMode;
                  return (
                    <span
                      key={m}
                      onClick={() => ((m === "PREVIEW") !== previewMode ? togglePreview() : undefined)}
                      title={m === "PREVIEW" ? "rendered exactly as the site publishes it — same markdown engine" : "raw markdown"}
                      style={{ fontFamily: font.mono, fontSize: 9, padding: "2px 9px", borderRadius: 3, cursor: "pointer", letterSpacing: 1, border: `1px solid ${active ? C.ember : C.line}`, color: active ? C.ember : C.dim }}
                    >
                      {m}
                    </span>
                  );
                })}
              </div>
              {previewMode ? (
                <div
                  className="md-preview"
                  // our own markdown engine over the operator's own text — the
                  // same HTML the static site publishes
                  dangerouslySetInnerHTML={{ __html: previewHtml }}
                  style={{ color: C.txt, fontFamily: font.body, fontSize: 13.5, lineHeight: 1.7, maxWidth: 760 }}
                />
              ) : (
                <textarea
                  value={bodyEdit}
                  onChange={(e) => setBodyEdit(e.target.value)}
                  readOnly={d.status === "published"}
                  spellCheck={false}
                  style={{ width: "100%", minHeight: 300, background: "transparent", border: "none", outline: "none", resize: "vertical", color: C.txt, fontFamily: font.body, fontSize: 13.5, lineHeight: 1.65 }}
                />
              )}
              <div style={{ fontFamily: font.mono, fontSize: 9, color: C.faint, marginTop: 6 }}>
                [#id] citations reference the ledger facts below · evacuation text is verbatim from officials
              </div>
            </div>
            <div style={{ padding: "10px 16px", borderTop: `1px solid ${C.line}`, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <button onClick={save} disabled={d.status === "published"} style={{ background: C.panelUp, color: C.txt, border: `1px solid ${C.line}`, borderRadius: 5, fontFamily: font.disp, fontWeight: 600, fontSize: 12, letterSpacing: 1, padding: "7px 14px", cursor: "pointer" }}>
                SAVE
              </button>
              <button onClick={copyBody} title="copy the full text to clipboard" style={{ background: C.panelUp, color: C.txt, border: `1px solid ${C.line}`, borderRadius: 5, fontFamily: font.disp, fontWeight: 600, fontSize: 12, letterSpacing: 1, padding: "7px 14px", cursor: "pointer" }}>
                ⧉ COPY
              </button>
              {(d.incident_id || (d.title ?? "").startsWith("CALIFORNIA WILDFIRE ROUNDUP")) && (
                <button
                  onClick={doGraphic}
                  title="render a map + stats card from the ledger; rides along on image-capable channels"
                  style={{ background: C.panelUp, color: C.warn, border: `1px solid ${C.warn}55`, borderRadius: 5, fontFamily: font.disp, fontWeight: 600, fontSize: 12, letterSpacing: 1, padding: "7px 14px", cursor: "pointer" }}
                >
                  {graphicBusy ? "…" : "🗺 GRAPHIC"}
                </button>
              )}
              {d.kind !== "social" && (
                <button onClick={makeSocial} title="rewrite in a human voice for posting (full + X-length)" style={{ background: C.panelUp, color: C.blue, border: `1px solid ${C.blue}55`, borderRadius: 5, fontFamily: font.disp, fontWeight: 600, fontSize: 12, letterSpacing: 1, padding: "7px 14px", cursor: "pointer" }}>
                  {socialBusy ? "…" : "✍ SOCIAL VERSION"}
                </button>
              )}
              <span style={{ fontFamily: font.mono, fontSize: 9, color: C.faint }}>AI drafts · you approve · nothing publishes itself</span>
              {d.kind === "social" && (d.status === "approved" || d.status === "published") && (
                <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", border: `1px solid ${C.ok}44`, borderRadius: 6, padding: "5px 9px" }}>
                  {channels.length === 0 && (
                    <span style={{ fontFamily: font.mono, fontSize: 9, color: C.faint }}>
                      no channels configured — OPTIONS → API KEYS
                    </span>
                  )}
                  {channels.map((c) => (
                    <span
                      key={c}
                      onClick={() =>
                        setSelChannels((cur) => {
                          const next = new Set(cur);
                          if (next.has(c)) next.delete(c);
                          else next.add(c);
                          return next;
                        })
                      }
                      style={{ fontFamily: font.mono, fontSize: 9.5, cursor: "pointer", color: selChannels.has(c) ? C.ok : C.dim, border: `1px solid ${selChannels.has(c) ? C.ok + "66" : C.line}`, borderRadius: 3, padding: "2px 7px" }}
                    >
                      {selChannels.has(c) ? "◼" : "◻"} {c.toUpperCase()}
                    </span>
                  ))}
                  <input
                    type="datetime-local"
                    value={schedAt}
                    onChange={(e) => setSchedAt(e.target.value)}
                    title="leave empty to release immediately"
                    style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: schedAt ? C.warn : C.dim, fontFamily: font.mono, fontSize: 9.5, padding: "3px 6px" }}
                  />
                  {channels.length > 0 && (
                    <button
                      onClick={doRelease}
                      disabled={releasing}
                      style={{ background: C.ok, color: C.bg, border: "none", borderRadius: 5, fontFamily: font.disp, fontWeight: 700, fontSize: 11.5, letterSpacing: 1, padding: "6px 14px", cursor: "pointer" }}
                    >
                      {releasing ? "…" : schedAt ? "⏱ SCHEDULE" : "⇧ RELEASE"}
                    </button>
                  )}
                </div>
              )}
              {nextActions.map(([status, label, color]) => (
                <button
                  key={status}
                  onClick={() => advance(status)}
                  style={{ marginLeft: status === nextActions[0][0] ? "auto" : 0, background: color, color: C.bg, border: "none", borderRadius: 5, fontFamily: font.disp, fontWeight: 700, fontSize: 12, letterSpacing: 1, padding: "7px 16px", cursor: "pointer" }}
                >
                  {label}
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      {/* cited facts */}
      <div style={{ borderLeft: `1px solid ${C.line}`, background: C.panel, display: "flex", flexDirection: "column", minHeight: 0 }}>
        {d && (
          <div style={{ padding: "10px 13px", borderBottom: `1px solid ${C.line}` }}>
            <div style={{ fontFamily: font.disp, fontSize: 12, letterSpacing: 2, color: C.dim, marginBottom: 7 }}>
              SOCIAL PUSH
            </div>
            {d.kind === "social" ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 10 }}>
                {/* the full roster, always visible — a channel without keys is a
                    to-do, not a secret */}
                {(["bluesky", "x", "facebook", "telegram"] as const).map((c) => {
                  const configured = channels.includes(c);
                  // the light tells the truth from the live credential check:
                  // green = tested ok, red = no keys or the check failed
                  const cred = credStatus[c];
                  const lightOk = configured && cred !== "failed";
                  const light = lightOk ? C.ok : C.evac;
                  const ready = lightOk && (d.status === "approved" || d.status === "published");
                  const pub = detail?.publications?.find((p) => p.channel === c);
                  const state = pub?.status; // sent | queued | failed | unknown
                  const active = ready && !state;
                  return (
                    <div key={c} style={{ display: "flex", alignItems: "center", gap: 7 }}>
                      <span
                        title={lightOk ? `${c} connected and ready` : cred === "failed" ? `${c} keys failing the connection test — OPTIONS → API KEYS` : `no ${c} keys — OPTIONS → API KEYS`}
                        style={{ width: 8, height: 8, borderRadius: 4, background: light, boxShadow: `0 0 5px ${light}`, flexShrink: 0 }}
                      />
                      <span
                        onClick={() => active && pushOne(c)}
                        title={!lightOk
                          ? `add ${c} keys in OPTIONS → API KEYS to enable`
                          : state
                            ? `already ${state} on ${c} — see delivery below`
                            : ready
                              ? `push this post to ${c} now (an already-sent channel is never doubled)`
                              : "approve the draft first — nothing publishes itself"}
                        style={{ flex: 1, fontFamily: font.mono, fontSize: 10, fontWeight: active ? 700 : 400, padding: "4px 10px", borderRadius: 4, cursor: active ? "pointer" : "not-allowed", background: active ? C.ok + "1a" : "transparent", border: `1px solid ${active ? C.ok : state === "sent" ? C.ok + "44" : C.line}`, color: !lightOk ? C.faint : state === "sent" ? C.ok : state ? C.dim : ready ? C.ok : C.dim, textAlign: "center", letterSpacing: 1 }}
                      >
                        {state === "sent" ? "✓ SENT" : state === "queued" ? "⏱ QUEUED" : state === "unknown" ? "? UNKNOWN" : state === "failed" ? "✗ FAILED" : "→ PUSH"} · {c.toUpperCase()}
                      </span>
                      {!configured && (
                        <span style={{ fontFamily: font.mono, fontSize: 7.5, color: C.faint, whiteSpace: "nowrap" }}>
                          no keys
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : (
              <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginBottom: 8 }}>
                reports publish to the site; social channels take the ✍ SOCIAL VERSION
              </div>
            )}
            <div style={{ fontFamily: font.disp, fontSize: 12, letterSpacing: 2, color: C.dim, marginBottom: 7 }}>
              EXPORT
            </div>
            <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
              <span
                onClick={() => d && download(`${(d.title || d.id).slice(0, 40)}.md`, bodyEdit, "text/markdown")}
                title="download the draft as markdown"
                style={{ fontFamily: font.mono, fontSize: 9.5, padding: "3px 9px", borderRadius: 4, cursor: "pointer", border: `1px solid ${C.blue}55`, color: C.blue }}
              >
                ⤓ .MD
              </span>
              <span
                onClick={() => d && download(`${(d.title || d.id).slice(0, 40)}.txt`, bodyEdit.replace(/[#*_`>\[\]]/g, ""), "text/plain")}
                title="download as plain text (markdown stripped)"
                style={{ fontFamily: font.mono, fontSize: 9.5, padding: "3px 9px", borderRadius: 4, cursor: "pointer", border: `1px solid ${C.blue}55`, color: C.blue }}
              >
                ⤓ .TXT
              </span>
              {detail?.draft.graphic_sha && (
                <a
                  href={`/api/drafts/${d.id}/graphic.png`}
                  download={`${d.id}-card.png`}
                  title="download the incident card"
                  style={{ fontFamily: font.mono, fontSize: 9.5, padding: "3px 9px", borderRadius: 4, cursor: "pointer", border: `1px solid ${C.blue}55`, color: C.blue, textDecoration: "none" }}
                >
                  ⤓ CARD
                </a>
              )}
              <span
                onClick={doBuildSite}
                title="rebuild the public static site from everything published"
                style={{ fontFamily: font.mono, fontSize: 9.5, padding: "3px 9px", borderRadius: 4, cursor: "pointer", border: `1px solid ${C.warn}55`, color: C.warn }}
              >
                ⌂ BUILD SITE
              </span>
            </div>
          </div>
        )}
        <div style={{ padding: "10px 13px", fontFamily: font.disp, fontSize: 12, letterSpacing: 2, color: C.dim, borderBottom: `1px solid ${C.line}` }}>
          CITED FACTS {detail ? `· ${detail.cited_facts.length}` : ""}
        </div>

        {/* Delivery outcomes. 'unknown' is the one that matters: the post may
            have landed and we never heard back, so re-sending risks a duplicate
            on a public account. Only a human who has looked can resolve it. */}
        {!!detail?.publications?.length && (
          <div style={{ padding: "8px 13px", borderBottom: `1px solid ${C.line}` }}>
            {detail.publications.map((p) => {
              const color =
                p.status === "sent" ? C.ok
                : p.status === "unknown" ? C.warn
                : p.status === "failed" ? C.evac
                : C.dim;
              return (
                <div
                  key={p.id}
                  title={p.status === "unknown"
                    ? "delivery indeterminate — check the account before re-sending"
                    : p.receipt ?? undefined}
                  style={{ display: "flex", gap: 6, alignItems: "center", fontFamily: font.mono, fontSize: 9, marginTop: 3 }}
                >
                  <span style={{ width: 6, height: 6, borderRadius: 3, background: color }} />
                  <span style={{ color: C.dim, minWidth: 58 }}>{p.channel.toUpperCase()}</span>
                  <span style={{ color }}>
                    {p.status === "unknown"
                      ? "UNKNOWN — VERIFY MANUALLY"
                      : p.status === "queued" && p.not_before && new Date(p.not_before) > new Date()
                        ? `SCHEDULED ${p.not_before.slice(5, 16).replace("T", " ")}Z`
                        : p.status.toUpperCase()}
                  </span>
                  {p.status === "failed" && (
                    <span
                      onClick={() => doRetry(p.id)}
                      title="the platform answered no — nothing went out, so re-sending is safe"
                      style={{ color: C.blue, cursor: "pointer", border: `1px solid ${C.blue}55`, borderRadius: 3, padding: "0 5px" }}
                    >
                      ↻ RETRY
                    </span>
                  )}
                  {p.external_url && (
                    <a href={p.external_url} target="_blank" rel="noopener noreferrer" style={{ color: C.blue }}>⧉</a>
                  )}
                </div>
              );
            })}
          </div>
        )}
        {detail?.draft.graphic_sha && (
          <div style={{ padding: "8px 13px", borderBottom: `1px solid ${C.line}` }}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={`/api/drafts/${detail.draft.id}/graphic.png?t=${graphicNonce}`}
              alt="attached incident card"
              style={{ width: "100%", borderRadius: 6, border: `1px solid ${C.line}` }}
            />
            <a
              href={`/api/drafts/${detail.draft.id}/graphic.png`}
              download={`${detail.draft.id}-card.png`}
              style={{ fontFamily: font.mono, fontSize: 8.5, color: C.blue, textDecoration: "none" }}
            >
              ⤓ download card
            </a>
          </div>
        )}
        <div style={{ flex: 1, overflowY: "auto" }}>
          {(detail?.cited_facts ?? []).map((f) => (
            <div key={f.id} style={{ padding: "7px 11px", borderBottom: `1px solid ${C.line}44`, display: "flex", gap: 7 }}>
              <div style={{ width: 3, borderRadius: 2, background: TIER[f.tier], flexShrink: 0 }} />
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 11, textDecoration: f.superseded_by ? "line-through" : undefined, color: f.superseded_by ? C.faint : undefined }}>
                  {f.content}
                </div>
                {f.superseded_by && f.superseded_content && (
                  <div style={{ fontSize: 10.5, color: C.warn, marginTop: 1 }}>→ {f.superseded_content}</div>
                )}
                <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginTop: 2 }}>
                  {f.kind} · T{f.tier} · {f.source_name}
                  {f.superseded_by && <span style={{ color: C.evac }}> · SUPERSEDED</span>}
                </div>
              </div>
            </div>
          ))}
          {detail && !detail.cited_facts.length && (
            <div style={{ padding: 14, fontFamily: font.mono, fontSize: 9.5, color: C.faint }}>
              no citations on this draft
            </div>
          )}
        </div>
        <div style={{ padding: "10px 13px", fontFamily: font.disp, fontSize: 12, letterSpacing: 2, color: C.dim, borderTop: `1px solid ${C.line}` }}>
          DISTRIBUTION
        </div>
        <div style={{ padding: "8px 13px 14px", fontFamily: font.mono, fontSize: 9.5, color: C.faint, lineHeight: 1.6 }}>
          <span style={{ color: C.txt }}>Website — manual.</span> Nothing is pushed to any site.
          Use <span style={{ color: C.blue }}>⧉ COPY</span> and paste into your own site;
          “MARK RELEASED” only records that you cleared it.
          <div style={{ marginTop: 6 }}>
            <span style={{ color: C.violet }}>Social</span> — a SOCIAL draft posts automatically on
            release, but only to channels whose keys are set in OPTIONS → API KEYS. With none set,
            nothing leaves this machine.
          </div>
        </div>
      </div>
    </div>
  );
}
