// ---------------------------------------------------------------------------
// Application constants — single source of truth for limits & labels
// ---------------------------------------------------------------------------

export const ALLOWED_FILE_TYPES = ["pdf", "txt", "md"] as const;
export type AllowedFileType = (typeof ALLOWED_FILE_TYPES)[number];

export const MAX_FILE_SIZE_MB = 50;
export const MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024;
export const MAX_QUERY_LENGTH = 2000;
export const DOCUMENT_POLL_INTERVAL_MS = 3000;
export const CONVERSATIONS_STORAGE_KEY = "ailways_conversations";
export const MAX_CONVERSATIONS = 50;

export const STATUS_LABELS: Record<string, string> = {
  pending: "Queued",
  ingesting: "Processing",
  active: "Ready",
  failed: "Failed",
  pending_delete: "Deleting",
  deleted: "Deleted",
};

export const STATUS_VARIANT: Record<string, "success" | "warning" | "error" | "neutral"> = {
  pending: "warning",
  ingesting: "warning",
  active: "success",
  failed: "error",
  pending_delete: "neutral",
  deleted: "neutral",
};

// ---------------------------------------------------------------------------
// Transcription session constants
// ---------------------------------------------------------------------------

export const SESSION_STATUS_LABELS: Record<string, string> = {
  recording: "Recording",
  completed: "Completed",
  failed: "Failed",
};

export const SESSION_STATUS_VARIANT: Record<string, "success" | "warning" | "error" | "neutral"> = {
  recording: "warning",
  completed: "success",
  failed: "error",
};

export const VERDICT_LABELS: Record<string, string> = {
  pending: "Verifying",
  supported: "Supported",
  contradicted: "Contradicted",
  unverifiable: "Unverifiable",
};

export const VERDICT_VARIANT: Record<string, "success" | "warning" | "error" | "neutral"> = {
  pending: "neutral",
  supported: "success",
  contradicted: "error",
  unverifiable: "warning",
};
