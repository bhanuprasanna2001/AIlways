// ---------------------------------------------------------------------------
// TypeScript types — mirrors backend Pydantic schemas exactly
// ---------------------------------------------------------------------------

export type User = {
  id: string;
  name: string;
  email: string;
};

export type VaultRole = "owner" | "editor" | "viewer";

export type Vault = {
  id: string;
  name: string;
  description: string | null;
  is_active: boolean;
  role: VaultRole;
  document_count: number;
  created_at: string;
  updated_at: string;
};

export type DocumentStatus =
  | "pending"
  | "ingesting"
  | "active"
  | "failed"
  | "pending_delete"
  | "deleted";

export type Document = {
  id: string;
  original_filename: string;
  file_type: string;
  file_size_bytes: number | null;
  status: DocumentStatus;
  error_message: string | null;
  page_count: number | null;
  created_at: string;
};

export type Citation = {
  doc_title: string;
  section: string | null;
  page: number | null;
  quote: string;
};

export type QueryResponse = {
  answer: string;
  citations: Citation[];
  confidence: number;
  has_sufficient_evidence: boolean;
  chunks_used: number;
  retrieval_method: string;
  latency_ms: number;
};

export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  confidence?: number;
  has_sufficient_evidence?: boolean;
  chunks_used?: number;
  latency_ms?: number;
  created_at: string;
};

export type Conversation = {
  id: string;
  vault_id: string;
  vault_name: string;
  title: string;
  messages: Message[];
  created_at: string;
  updated_at: string;
};

// ---------------------------------------------------------------------------
// Transcription types — mirrors backend transcription schemas
// ---------------------------------------------------------------------------

export type TranscriptionSessionStatus = "recording" | "completed" | "failed";

export type TranscriptionSession = {
  id: string;
  vault_id: string;
  vault_name: string;
  title: string;
  status: TranscriptionSessionStatus;
  duration_seconds: number | null;
  speaker_count: number;
  segment_count: number;
  claim_count: number;
  started_at: string;
  ended_at: string | null;
};

export type TranscriptionSegment = {
  id: string;
  text: string;
  speaker: number;
  start: number;
  end: number;
  confidence: number;
  segment_index: number;
};

export type TranscriptionClaimVerdict =
  | "pending"
  | "supported"
  | "contradicted"
  | "unverifiable";

export type TranscriptionClaim = {
  id: string;
  text: string;
  speaker: number;
  timestamp_start: number;
  timestamp_end: number;
  context: string;
  verdict: TranscriptionClaimVerdict;
  confidence: number;
  explanation: string | null;
  evidence: Citation[];
};

export type TranscriptionSessionDetail = TranscriptionSession & {
  segments: TranscriptionSegment[];
  claims: TranscriptionClaim[];
};
