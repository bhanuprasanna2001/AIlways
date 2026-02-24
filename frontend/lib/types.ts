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
