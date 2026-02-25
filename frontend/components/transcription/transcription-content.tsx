"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import {
  Mic,
  Square,
  RotateCcw,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Loader2,
  Monitor,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { VERDICT_LABELS, VERDICT_VARIANT } from "@/lib/constants";
import type { Vault } from "@/lib/types";
import { fetcher } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/spinner";
import { EmptyState } from "@/components/ui/empty-state";
import {
  useTranscription,
  type LiveSegment,
  type LiveClaim,
  type AudioMode,
} from "@/hooks/use-transcription";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function formatTimestamp(secs: number): string {
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
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

const VERDICT_ICONS: Record<string, React.ReactNode> = {
  verifying: <Loader2 className="h-3.5 w-3.5 animate-spin" />,
  supported: <CheckCircle2 className="h-3.5 w-3.5 text-green-600 dark:text-green-400" />,
  contradicted: <XCircle className="h-3.5 w-3.5 text-red-600 dark:text-red-400" />,
  unverifiable: <AlertTriangle className="h-3.5 w-3.5 text-amber-600 dark:text-amber-400" />,
};

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SegmentList({ segments }: { segments: LiveSegment[] }) {
  if (segments.length === 0) return null;

  // Group consecutive segments by speaker AND time proximity.
  // Break into a new group when the gap exceeds 5 s so continuous
  // single-speaker speech doesn't collapse into one paragraph.
  const SEGMENT_GROUP_GAP_S = 5;
  const groups: { speaker: number; texts: string[]; start: number; end: number }[] = [];
  for (const seg of segments) {
    const last = groups[groups.length - 1];
    const gapExceeded = last ? seg.start - last.end > SEGMENT_GROUP_GAP_S : false;
    if (last && last.speaker === seg.speaker && !gapExceeded) {
      last.texts.push(seg.text);
      last.end = seg.end;
    } else {
      groups.push({ speaker: seg.speaker, texts: [seg.text], start: seg.start, end: seg.end });
    }
  }

  return (
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
            <p className="text-sm text-foreground">{g.texts.join(" ")}</p>
          </div>
        </div>
      ))}
    </div>
  );
}

function ClaimCard({ claim }: { claim: LiveClaim }) {
  return (
    <div className="rounded-lg border border-neutral-200 px-3 py-2.5 dark:border-neutral-700">
      <div className="flex items-start gap-2">
        <span className="mt-0.5">{VERDICT_ICONS[claim.verdict]}</span>
        <div className="flex-1">
          <p className="text-sm text-foreground">{claim.text}</p>
          <div className="mt-1 flex items-center gap-2">
            <Badge variant={VERDICT_VARIANT[claim.verdict] ?? "neutral"}>
              {VERDICT_LABELS[claim.verdict] ?? claim.verdict}
            </Badge>
            <span
              className={cn(
                "text-xs",
                speakerColor(claim.speaker),
              )}
            >
              Speaker {claim.speaker + 1}
            </span>
          </div>
          {claim.explanation && (
            <p className="mt-1.5 text-xs text-neutral-500 dark:text-neutral-400">
              {claim.explanation}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// TranscriptionContent
// ---------------------------------------------------------------------------

export default function TranscriptionContent() {
  const router = useRouter();
  const { data: vaults } = useSWR<Vault[]>("/api/vaults", fetcher);

  const [selectedVaultId, setSelectedVaultId] = useState("");
  const [audioMode, setAudioMode] = useState<AudioMode>("mic");
  const {
    status,
    segments,
    claims,
    sessionId,
    duration,
    error,
    elapsed,
    systemAudioActive,
    start,
    stop,
    reset,
  } = useTranscription();

  // Auto-select first vault
  if (vaults?.length && !selectedVaultId) {
    setSelectedVaultId(vaults[0].id);
  }

  const isActive = status === "recording" || status === "connecting";
  const isDone = status === "idle" && segments.length > 0;
  const selectedVault = vaults?.find((v) => v.id === selectedVaultId);

  // No vaults
  if (vaults && vaults.length === 0) {
    return (
      <EmptyState
        icon={<Mic className="h-10 w-10" />}
        title="No vaults available"
        description="Create a vault and upload documents to verify claims against."
        action={
          <a
            href="/vaults"
            className="inline-flex h-9 items-center rounded-lg bg-foreground px-4 text-sm font-medium text-background transition-colors hover:opacity-90"
          >
            Go to Vaults
          </a>
        }
      />
    );
  }

  return (
    <div className="flex h-[calc(100vh-8rem)] flex-col">
      {/* Top bar */}
      <div className="flex items-center justify-between pb-4">
        <div className="flex items-center gap-3">
          <select
            value={selectedVaultId}
            onChange={(e) => setSelectedVaultId(e.target.value)}
            disabled={isActive}
            aria-label="Select vault"
            className="h-9 rounded-lg border border-neutral-200 bg-white px-3 pr-8 text-sm text-foreground outline-none disabled:opacity-50 dark:border-neutral-700 dark:bg-neutral-900"
          >
            {vaults?.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
              </option>
            ))}
          </select>
          {selectedVault?.document_count === 0 && (
            <Badge variant="warning">No documents</Badge>
          )}

          {/* Audio mode toggle */}
          <div className="flex h-9 items-center rounded-lg border border-neutral-200 p-0.5 dark:border-neutral-700">
            <button
              type="button"
              onClick={() => setAudioMode("mic")}
              disabled={isActive}
              className={cn(
                "inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-xs font-medium transition-colors disabled:opacity-50",
                audioMode === "mic"
                  ? "bg-foreground text-background"
                  : "text-neutral-500 hover:text-foreground dark:text-neutral-400",
              )}
            >
              <Mic className="h-3.5 w-3.5" />
              Mic Only
            </button>
            <button
              type="button"
              onClick={() => setAudioMode("meeting")}
              disabled={isActive}
              className={cn(
                "inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-xs font-medium transition-colors disabled:opacity-50",
                audioMode === "meeting"
                  ? "bg-foreground text-background"
                  : "text-neutral-500 hover:text-foreground dark:text-neutral-400",
              )}
            >
              <Monitor className="h-3.5 w-3.5" />
              Meeting
            </button>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {status === "recording" && (
            <div className="flex items-center gap-2 text-sm text-neutral-500">
              <span className="relative flex h-2.5 w-2.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-400 opacity-75" />
                <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-red-500" />
              </span>
              {formatTime(elapsed)}
              {systemAudioActive && (
                <Badge variant="success">
                  <Monitor className="mr-1 h-3 w-3" />
                  Meeting
                </Badge>
              )}
            </div>
          )}

          {isDone && sessionId && (
            <button
              onClick={() => router.push(`/sessions?session=${sessionId}`)}
              className="text-sm font-medium text-neutral-500 hover:text-foreground dark:text-neutral-400"
            >
              View session
            </button>
          )}

          {isDone && (
            <button
              onClick={reset}
              className="inline-flex items-center gap-1.5 text-sm font-medium text-neutral-500 hover:text-foreground dark:text-neutral-400"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              New session
            </button>
          )}
        </div>
      </div>

      {/* Main content */}
      <div className="flex flex-1 gap-6 overflow-hidden">
        {/* Transcript panel */}
        <div className="flex-1 overflow-y-auto pr-2">
          {status === "connecting" ? (
            <div className="flex h-full items-center justify-center">
              <div className="text-center">
                <Spinner className="mx-auto h-6 w-6" />
                <p className="mt-3 text-sm text-neutral-500 dark:text-neutral-400">
                  Connecting…
                </p>
              </div>
            </div>
          ) : segments.length === 0 && !isActive ? (
            <div className="flex h-full items-center justify-center">
              <div className="text-center">
                <Mic className="mx-auto h-10 w-10 text-neutral-300 dark:text-neutral-600" />
                <p className="mt-3 text-sm text-neutral-500 dark:text-neutral-400">
                  Start recording to transcribe audio
                </p>
                {duration !== null && (
                  <p className="mt-1 text-xs text-neutral-400 dark:text-neutral-500">
                    Last session: {formatTime(Math.round(duration))}
                  </p>
                )}
              </div>
            </div>
          ) : (
            <SegmentList segments={segments} />
          )}
        </div>

        {/* Claims panel */}
        {(claims.length > 0 || isActive) && (
          <div className="w-80 shrink-0 overflow-y-auto border-l border-neutral-200 pl-6 dark:border-neutral-800">
            <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-neutral-400">
              Claims ({claims.length})
            </h3>
            {claims.length === 0 ? (
              <p className="text-xs text-neutral-400 dark:text-neutral-500">
                Claims will appear here as they are detected…
              </p>
            ) : (
              <div className="space-y-2">
                {claims.map((claim) => (
                  <ClaimCard key={claim.id} claim={claim} />
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Error banner */}
      {error && (
        <div className="mb-2 rounded-lg bg-red-50 px-4 py-2.5 text-sm text-red-600 dark:bg-red-950/50 dark:text-red-400">
          {error}
        </div>
      )}

      {/* Controls */}
      <div className="border-t border-neutral-200 pt-4 dark:border-neutral-800">
        <div className="flex justify-center">
          {status === "idle" || status === "error" ? (
            <button
              onClick={() => selectedVaultId && start(selectedVaultId, audioMode)}
              disabled={!selectedVaultId || selectedVault?.document_count === 0}
              className="inline-flex h-12 items-center gap-2 rounded-full bg-foreground px-6 text-sm font-medium text-background transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Mic className="h-5 w-5" />
              Start Recording
            </button>
          ) : status === "connecting" ? (
            <button
              disabled
              className="inline-flex h-12 items-center gap-2 rounded-full bg-foreground px-6 text-sm font-medium text-background opacity-50"
            >
              <Spinner className="h-5 w-5 text-background" />
              Connecting…
            </button>
          ) : status === "recording" ? (
            <button
              onClick={stop}
              className="inline-flex h-12 items-center gap-2 rounded-full bg-red-600 px-6 text-sm font-medium text-white transition-colors hover:bg-red-700"
            >
              <Square className="h-4 w-4" />
              Stop Recording
            </button>
          ) : (
            <button
              disabled
              className="inline-flex h-12 items-center gap-2 rounded-full bg-foreground px-6 text-sm font-medium text-background opacity-50"
            >
              <Spinner className="h-5 w-5 text-background" />
              Stopping…
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
