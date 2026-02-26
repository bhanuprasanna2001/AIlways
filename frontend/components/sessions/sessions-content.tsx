"use client";

import { useState, useMemo, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR, { mutate } from "swr";
import {
  Mic,
  Search,
  Trash2,
  Clock,
  Users,
  MessageSquare,
  ChevronLeft,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Pencil,
} from "lucide-react";
import { cn, formatRelativeTime, truncate } from "@/lib/utils";
import {
  SESSION_STATUS_LABELS,
  SESSION_STATUS_VARIANT,
  VERDICT_LABELS,
  VERDICT_VARIANT,
} from "@/lib/constants";
import type {
  TranscriptionSession,
  TranscriptionSessionDetail,
} from "@/lib/types";
import { apiFetch, fetcher } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/spinner";
import { EmptyState } from "@/components/ui/empty-state";
import { Modal } from "@/components/ui/modal";
import { Pagination, paginate } from "@/components/ui/pagination";
import { CitationCard } from "@/components/ui/citation-card";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m === 0) return `${s}s`;
  return `${m}m ${s}s`;
}

const SPEAKER_COLORS = [
  "text-blue-600 dark:text-blue-400",
  "text-emerald-600 dark:text-emerald-400",
  "text-purple-600 dark:text-purple-400",
  "text-orange-600 dark:text-orange-400",
  "text-pink-600 dark:text-pink-400",
  "text-cyan-600 dark:text-cyan-400",
];

function speakerColor(speaker: number): string {
  return SPEAKER_COLORS[speaker % SPEAKER_COLORS.length];
}

function formatTimestamp(secs: number): string {
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

const VERDICT_ICONS: Record<string, React.ReactNode> = {
  pending: <Clock className="h-3.5 w-3.5 text-neutral-400" />,
  supported: <CheckCircle2 className="h-3.5 w-3.5 text-green-600 dark:text-green-400" />,
  contradicted: <XCircle className="h-3.5 w-3.5 text-red-600 dark:text-red-400" />,
  unverifiable: <AlertTriangle className="h-3.5 w-3.5 text-amber-600 dark:text-amber-400" />,
};

// ---------------------------------------------------------------------------
// Session detail view
// ---------------------------------------------------------------------------

function SessionDetail({
  sessionId,
  onBack,
}: {
  sessionId: string;
  onBack: () => void;
}) {
  const { data: session, isLoading } = useSWR<TranscriptionSessionDetail>(
    `/api/sessions/${sessionId}`,
    fetcher,
  );

  if (isLoading || !session) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner className="h-6 w-6" />
      </div>
    );
  }

  // Group segments by speaker AND time proximity.
  // Start a new group when speaker changes OR when the gap between
  // consecutive segments exceeds SEGMENT_GROUP_GAP_S.  This prevents
  // a 60-second single-speaker session from collapsing into one wall
  // of text — pauses in speech create natural visual breaks.
  const SEGMENT_GROUP_GAP_S = 5;
  const groups: { speaker: number; texts: string[]; start: number; end: number }[] = [];
  for (const seg of session.segments) {
    const last = groups[groups.length - 1];
    const gapExceeded = last ? seg.start - last.end > SEGMENT_GROUP_GAP_S : false;
    if (last && last.speaker === seg.speaker && !gapExceeded) {
      last.texts.push(seg.text);
      last.end = seg.end;
    } else {
      groups.push({
        speaker: seg.speaker,
        texts: [seg.text],
        start: seg.start,
        end: seg.end,
      });
    }
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <button
          onClick={onBack}
          className="rounded-md p-1 text-neutral-400 transition-colors hover:text-foreground"
        >
          <ChevronLeft className="h-5 w-5" />
        </button>
        <div className="flex-1">
          <h2 className="text-lg font-semibold text-foreground">
            {session.title}
          </h2>
          <p className="text-xs text-neutral-500 dark:text-neutral-400">
            {session.vault_name} · {formatDuration(session.duration_seconds)} ·{" "}
            {session.speaker_count} speaker
            {session.speaker_count !== 1 ? "s" : ""} ·{" "}
            {formatRelativeTime(session.started_at)}
          </p>
        </div>
        <Badge variant={SESSION_STATUS_VARIANT[session.status] ?? "neutral"}>
          {SESSION_STATUS_LABELS[session.status] ?? session.status}
        </Badge>
      </div>

      {/* Content — two columns */}
      <div className="flex gap-6">
        {/* Transcript */}
        <div className="flex-1 space-y-3">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-400">
            Transcript ({session.segment_count} segments)
          </h3>
          {groups.length === 0 ? (
            <p className="text-sm text-neutral-400">No segments recorded.</p>
          ) : (
            <div className="space-y-3">
              {groups.map((g, i) => (
                <div key={i} className="flex gap-3">
                  <span className="mt-0.5 shrink-0 text-[10px] font-mono text-neutral-400">
                    {formatTimestamp(g.start)}
                  </span>
                  <div>
                    <span
                      className={cn(
                        "text-xs font-semibold",
                        speakerColor(g.speaker),
                      )}
                    >
                      Speaker {g.speaker + 1}
                    </span>
                    <p className="text-sm text-foreground">
                      {g.texts.join(" ")}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Claims */}
        {session.claims.length > 0 && (
          <div className="w-80 shrink-0 overflow-hidden border-l border-neutral-200 pl-6 dark:border-neutral-800">
            <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-neutral-400">
              Claims ({session.claim_count})
            </h3>
            <div className="space-y-2">
              {session.claims.map((claim) => (
                <div
                  key={claim.id}
                  className="overflow-hidden rounded-lg border border-neutral-200 px-3 py-2.5 dark:border-neutral-700"
                >
                  <div className="flex items-start gap-2">
                    <span className="mt-0.5">
                      {VERDICT_ICONS[claim.verdict]}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm text-foreground">{claim.text}</p>
                      <div className="mt-1 flex items-center gap-2">
                        <Badge
                          variant={
                            VERDICT_VARIANT[claim.verdict] ?? "neutral"
                          }
                        >
                          {VERDICT_LABELS[claim.verdict] ?? claim.verdict}
                        </Badge>
                        <span
                          className={cn("text-xs", speakerColor(claim.speaker))}
                        >
                          Speaker {claim.speaker + 1}
                        </span>
                      </div>
                      {claim.explanation && (
                        <p className="mt-1.5 text-xs text-neutral-500 dark:text-neutral-400">
                          {claim.explanation}
                        </p>
                      )}
                      {claim.evidence && claim.evidence.length > 0 && (
                        <div className="mt-2 space-y-1.5">
                          <p className="text-[10px] font-semibold uppercase tracking-wider text-neutral-400">
                            Sources ({claim.evidence.length})
                          </p>
                          {claim.evidence.map((cit, idx) => (
                            <CitationCard key={idx} citation={cit} index={idx} />
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Session list view
// ---------------------------------------------------------------------------

export default function SessionsContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const selectedSessionId = searchParams.get("session");

  const { data: sessions, isLoading } = useSWR<TranscriptionSession[]>(
    "/api/sessions",
    fetcher,
  );

  const [search, setSearch] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [renameTarget, setRenameTarget] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [actionLoading, setActionLoading] = useState(false);
  const [page, setPage] = useState(1);
  const ITEMS_PER_PAGE = 10;

  const filtered = useMemo(() => {
    if (!sessions) return [];
    if (!search) return sessions;
    const term = search.toLowerCase();
    return sessions.filter(
      (s) =>
        s.title.toLowerCase().includes(term) ||
        s.vault_name.toLowerCase().includes(term),
    );
  }, [sessions, search]);

  const { paged, totalPages } = useMemo(
    () => paginate(filtered, page, ITEMS_PER_PAGE),
    [filtered, page],
  );

  // Reset to page 1 when search changes
  useEffect(() => {
    setPage(1);
  }, [search]);

  // ---- Detail view ----
  if (selectedSessionId) {
    return (
      <SessionDetail
        sessionId={selectedSessionId}
        onBack={() => router.replace("/sessions")}
      />
    );
  }

  // ---- Loading ----
  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner className="h-6 w-6" />
      </div>
    );
  }

  // ---- Empty ----
  if (!sessions || sessions.length === 0) {
    return (
      <EmptyState
        icon={<Mic className="h-10 w-10" />}
        title="No transcription sessions"
        description="Start a live transcription to see your sessions here."
        action={
          <a
            href="/transcription"
            className="inline-flex h-9 items-center rounded-lg bg-foreground px-4 text-sm font-medium text-background transition-colors hover:opacity-90"
          >
            Start Transcription
          </a>
        }
      />
    );
  }

  // ---- Handlers ----
  async function handleDelete() {
    if (!deleteTarget) return;
    setActionLoading(true);
    try {
      await apiFetch(`/api/sessions/${deleteTarget}`, { method: "DELETE" });
      mutate("/api/sessions");
    } catch {
      /* ignore */
    } finally {
      setDeleteTarget(null);
      setActionLoading(false);
    }
  }

  async function handleRename() {
    if (!renameTarget || !renameValue.trim()) return;
    setActionLoading(true);
    try {
      await apiFetch(`/api/sessions/${renameTarget}`, {
        method: "PATCH",
        body: { title: renameValue.trim() },
      });
      mutate("/api/sessions");
    } catch {
      /* ignore */
    } finally {
      setRenameTarget(null);
      setRenameValue("");
      setActionLoading(false);
    }
  }

  return (
    <div className="space-y-4">
      {/* Search */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search sessions…"
            aria-label="Search sessions"
            className="h-9 w-full rounded-lg border border-neutral-200 bg-white pl-9 pr-3.5 text-sm text-foreground outline-none placeholder:text-neutral-400 focus:border-foreground dark:border-neutral-700 dark:bg-neutral-900 dark:placeholder:text-neutral-500"
          />
        </div>
      </div>

      {/* Session list */}
      {filtered.length === 0 ? (
        <p className="py-8 text-center text-sm text-neutral-500 dark:text-neutral-400">
          No sessions match your search
        </p>
      ) : (
        <>
        <div className="space-y-2">
          {paged.map((session) => (
            <div
              key={session.id}
              className="group flex items-center justify-between rounded-xl border border-neutral-200 px-4 py-3 transition-colors hover:bg-neutral-50 dark:border-neutral-800 dark:hover:bg-neutral-800/30"
            >
              <button
                onClick={() =>
                  router.push(`/sessions?session=${session.id}`)
                }
                className="flex-1 text-left"
              >
                <div className="flex items-center gap-2">
                  <p className="text-sm font-medium text-foreground">
                    {truncate(session.title, 80)}
                  </p>
                  <Badge
                    variant={
                      SESSION_STATUS_VARIANT[session.status] ?? "neutral"
                    }
                  >
                    {SESSION_STATUS_LABELS[session.status] ?? session.status}
                  </Badge>
                </div>
                <p className="mt-0.5 flex items-center gap-2 text-xs text-neutral-500 dark:text-neutral-400">
                  <span>{session.vault_name}</span>
                  <span>·</span>
                  <span className="inline-flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    {formatDuration(session.duration_seconds)}
                  </span>
                  <span>·</span>
                  <span className="inline-flex items-center gap-1">
                    <Users className="h-3 w-3" />
                    {session.speaker_count}
                  </span>
                  <span>·</span>
                  <span className="inline-flex items-center gap-1">
                    <MessageSquare className="h-3 w-3" />
                    {session.claim_count} claim
                    {session.claim_count !== 1 ? "s" : ""}
                  </span>
                  <span>·</span>
                  <span>{formatRelativeTime(session.started_at)}</span>
                </p>
              </button>
              <div className="ml-4 flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                <button
                  onClick={() => {
                    setRenameTarget(session.id);
                    setRenameValue(session.title);
                  }}
                  className="rounded p-1 text-neutral-300 transition-colors hover:text-foreground dark:text-neutral-600 dark:hover:text-neutral-300"
                  title="Rename session"
                >
                  <Pencil className="h-4 w-4" />
                </button>
                <button
                  onClick={() => setDeleteTarget(session.id)}
                  className="rounded p-1 text-neutral-300 transition-colors hover:text-red-500 dark:text-neutral-600 dark:hover:text-red-400"
                  title="Delete session"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
        <Pagination page={page} totalPages={totalPages} onPageChange={setPage} />
        </>
      )}

      {/* Delete confirmation */}
      <Modal
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        title="Delete session"
      >
        <p className="text-sm text-neutral-500 dark:text-neutral-400">
          This session and all its transcript data will be permanently deleted.
          This action cannot be undone.
        </p>
        <div className="mt-4 flex justify-end gap-3">
          <button
            onClick={() => setDeleteTarget(null)}
            disabled={actionLoading}
            className="rounded-lg px-4 py-2 text-sm text-neutral-600 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-800"
          >
            Cancel
          </button>
          <button
            onClick={handleDelete}
            disabled={actionLoading}
            className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
          >
            {actionLoading ? "Deleting…" : "Delete"}
          </button>
        </div>
      </Modal>

      {/* Rename modal */}
      <Modal
        open={!!renameTarget}
        onClose={() => setRenameTarget(null)}
        title="Rename session"
      >
        <input
          type="text"
          value={renameValue}
          onChange={(e) => setRenameValue(e.target.value)}
          placeholder="Session title"
          className="h-9 w-full rounded-lg border border-neutral-200 bg-white px-3 text-sm text-foreground outline-none placeholder:text-neutral-400 focus:border-foreground dark:border-neutral-700 dark:bg-neutral-900"
          onKeyDown={(e) => {
            if (e.key === "Enter") handleRename();
          }}
        />
        <div className="mt-4 flex justify-end gap-3">
          <button
            onClick={() => setRenameTarget(null)}
            disabled={actionLoading}
            className="rounded-lg px-4 py-2 text-sm text-neutral-600 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-800"
          >
            Cancel
          </button>
          <button
            onClick={handleRename}
            disabled={actionLoading || !renameValue.trim()}
            className="rounded-lg bg-foreground px-4 py-2 text-sm font-medium text-background hover:opacity-90 disabled:opacity-50"
          >
            {actionLoading ? "Saving…" : "Save"}
          </button>
        </div>
      </Modal>
    </div>
  );
}
