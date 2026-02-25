"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import useSWR from "swr";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Send,
  ChevronDown,
  ChevronRight,
  MessageSquare,
  Plus,
  FileText,
  AlertCircle,
  FolderLock,
} from "lucide-react";
import { apiFetch, fetcher } from "@/lib/api";
import { cn, generateId, truncate } from "@/lib/utils";
import { MAX_QUERY_LENGTH } from "@/lib/constants";
import type {
  Vault,
  Message,
  Conversation,
  QueryResponse,
  Citation,
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/spinner";
import { EmptyState } from "@/components/ui/empty-state";
import { useConversations } from "@/hooks/use-conversations";

// ---------------------------------------------------------------------------
// CitationCard — renders each source with proper markdown formatting
// ---------------------------------------------------------------------------

function CitationCard({ citation, index }: { citation: Citation; index: number }) {
  const [expanded, setExpanded] = useState(false);

  // Determine if the quote is long enough to warrant collapse
  const isLong = citation.quote.length > 200;

  return (
    <div className="rounded-lg border border-neutral-200 transition-colors hover:border-neutral-300 dark:border-neutral-700 dark:hover:border-neutral-600">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <FileText className="h-3.5 w-3.5 shrink-0 text-neutral-400" />
        <span className="flex-1 truncate text-xs font-medium text-foreground">
          {citation.doc_title}
          {citation.section && (
            <span className="text-neutral-400"> · {citation.section}</span>
          )}
          {citation.page && (
            <span className="text-neutral-400"> · p.{citation.page}</span>
          )}
        </span>
        <span className="shrink-0 rounded bg-neutral-100 px-1.5 py-0.5 text-[10px] font-mono text-neutral-500 dark:bg-neutral-800">
          [{index + 1}]
        </span>
        {isLong && (
          expanded ? (
            <ChevronDown className="h-3 w-3 shrink-0 text-neutral-400" />
          ) : (
            <ChevronRight className="h-3 w-3 shrink-0 text-neutral-400" />
          )
        )}
      </button>
      <div
        className={cn(
          "border-t border-neutral-100 px-3 py-2 dark:border-neutral-800",
          isLong && !expanded && "max-h-24 overflow-hidden",
        )}
      >
        <div className="prose prose-xs max-w-none text-xs text-neutral-600 dark:prose-invert dark:text-neutral-400 prose-p:my-1 prose-table:my-1 prose-th:px-2 prose-th:py-1 prose-td:px-2 prose-td:py-1 prose-table:text-xs prose-table:border prose-th:border prose-td:border prose-th:bg-neutral-50 dark:prose-th:bg-neutral-800/50">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {citation.quote}
          </ReactMarkdown>
        </div>
        {isLong && !expanded && (
          <div className="relative -mt-6 h-6 bg-gradient-to-t from-white to-transparent dark:from-neutral-900" />
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// MessageBubble
// ---------------------------------------------------------------------------

function MessageBubble({ message }: { message: Message }) {
  const [showThinking, setShowThinking] = useState(false);

  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-2xl rounded-br-md bg-foreground px-4 py-2.5 text-sm text-background">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="max-w-[90%]">
        {/* Answer — rendered as markdown */}
        <div className="prose prose-sm max-w-none text-sm text-foreground dark:prose-invert prose-p:my-1.5 prose-headings:mb-2 prose-headings:mt-4 prose-pre:bg-neutral-100 dark:prose-pre:bg-neutral-800">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {message.content}
          </ReactMarkdown>
        </div>

        {/* Thought‐process toggle */}
        {message.confidence !== undefined && (
          <button
            onClick={() => setShowThinking(!showThinking)}
            className="mt-2 flex items-center gap-1.5 text-xs text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-300"
          >
            {showThinking ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
            Thinking
          </button>
        )}

        {showThinking && (
          <div className="mt-2 rounded-lg bg-neutral-50 p-3 text-xs text-neutral-500 dark:bg-neutral-800/50 dark:text-neutral-400">
            <div className="grid grid-cols-2 gap-2 font-mono">
              <div>
                Confidence:{" "}
                <span
                  className={cn(
                    message.confidence! >= 0.7
                      ? "text-green-600 dark:text-green-400"
                      : message.confidence! >= 0.4
                        ? "text-amber-600 dark:text-amber-400"
                        : "text-red-600 dark:text-red-400",
                  )}
                >
                  {(message.confidence! * 100).toFixed(0)}%
                </span>
              </div>
              <div>Chunks: {message.chunks_used}</div>
              <div>Latency: {message.latency_ms}ms</div>
              <div>
                Evidence:{" "}
                {message.has_sufficient_evidence
                  ? "✓ Sufficient"
                  : "✕ Insufficient"}
              </div>
            </div>
          </div>
        )}

        {/* Citations */}
        {message.citations && message.citations.length > 0 && (
          <div className="mt-3 space-y-2">
            <p className="text-xs font-medium text-neutral-400">
              Sources ({message.citations.length})
            </p>
            {message.citations.map((citation, i) => (
              <CitationCard key={i} citation={citation} index={i} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CopilotContent
// ---------------------------------------------------------------------------

export default function CopilotContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const conversationParam = searchParams.get("conversation");

  const { data: vaults } = useSWR<Vault[]>("/api/vaults", fetcher);
  const {
    isHydrated,
    addConversation,
    updateConversation,
    getConversation,
  } = useConversations();

  const [selectedVaultId, setSelectedVaultId] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [activeConvId, setActiveConvId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [isQuerying, setIsQuerying] = useState(false);
  const [error, setError] = useState("");

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Always-current messages ref — avoids stale-closure bugs in the async
  // handleSend callback which outlives the render that created it.
  const messagesRef = useRef(messages);
  messagesRef.current = messages;

  // --- Refs that coordinate async URL changes with the loading effect ---
  // Which conversation ID was last loaded/attempted from the URL.
  const lastLoadedConvRef = useRef<string | null>(null);
  // Set to `true` when an intentional reset (New Chat / Vault Switch)
  // calls router.replace. Prevents the loading effect from re-loading
  // the old conversation while the URL hasn't caught up yet.
  const pendingResetRef = useRef(false);

  // ---- Cleanup in-flight request on unmount ----
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  // ---- Conversation loading — single source of truth ----
  // `conversationParam` (from URL) drives which conversation to display.
  // Local state (messages, activeConvId) is derived from it on transitions.
  useEffect(() => {
    if (!isHydrated) return;

    // 1. Guard: intentional reset in progress (New Chat / Vault Switch).
    //    Wait for the URL to clear before allowing any further loading.
    if (pendingResetRef.current) {
      if (!conversationParam) {
        pendingResetRef.current = false;
        lastLoadedConvRef.current = null;
      }
      return;
    }

    // 2. URL has a conversation param → attempt to load it.
    if (conversationParam) {
      if (lastLoadedConvRef.current === conversationParam) {
        // Conversation already loaded — but vault might not have been resolved
        // if vaults weren't available at load time. Re-check now.
        if (!selectedVaultId && vaults?.length) {
          const conv = getConversation(conversationParam);
          if (conv && vaults.some((v) => v.id === conv.vault_id)) {
            setSelectedVaultId(conv.vault_id);
          }
        }
        return;
      }

      const conv = getConversation(conversationParam);
      if (conv) {
        setActiveConvId(conv.id);
        setMessages(conv.messages);
        // Only switch vault if it still exists in the user's vault list
        if (vaults?.some((v) => v.id === conv.vault_id)) {
          setSelectedVaultId(conv.vault_id);
        }
        lastLoadedConvRef.current = conversationParam;
      } else {
        // Conversation not found — mark as attempted to prevent loop,
        // then clear the stale URL.
        lastLoadedConvRef.current = conversationParam;
        router.replace("/copilot");
      }
      return;
    }

    // 3. URL has NO conversation param.
    //    If we previously loaded one from the URL, this means the user
    //    navigated away via an external route (sidebar "Copilot" link, etc.).
    //    → Clear conversation state to show the empty-chat screen.
    if (lastLoadedConvRef.current) {
      setMessages([]);
      setActiveConvId(null);
      setError("");
      lastLoadedConvRef.current = null;
      // Fall through to default vault selection.
    }

    // 4. Default vault selection — only when not viewing a conversation.
    //    When viewing a conversation whose vault is deleted, we leave
    //    selectedVaultId empty to signal read-only mode.
    if (vaults?.length && !selectedVaultId && !conversationParam) {
      setSelectedVaultId(vaults[0].id);
    }
  }, [isHydrated, conversationParam, getConversation, vaults, selectedVaultId, router]);

  // ---- Auto-scroll ----
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isQuerying]);

  // ---- Auto-resize textarea ----
  const handleQueryChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      setQuery(e.target.value);
      // Reset height to auto to recalculate, then set to scrollHeight
      const el = e.target;
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 160)}px`; // max ~6 lines
    },
    [],
  );

  // ---- Send message ----
  const handleSend = useCallback(async () => {
    const trimmed = query.trim();
    if (!trimmed || !selectedVaultId || isQuerying) return;

    setError("");
    setQuery("");
    // Reset textarea height after clearing
    if (inputRef.current) inputRef.current.style.height = "auto";
    setIsQuerying(true);

    const userMsg: Message = {
      id: generateId(),
      role: "user",
      content: trimmed,
      created_at: new Date().toISOString(),
    };

    // Use ref to guarantee we read the latest messages, not a
    // potentially stale closure captured at callback creation time.
    const currentMessages = messagesRef.current;
    const newMessages = [...currentMessages, userMsg];
    setMessages(newMessages);

    // Create or update conversation in localStorage
    let convId = activeConvId;
    if (!convId) {
      convId = generateId();
      const vaultName =
        vaults?.find((v) => v.id === selectedVaultId)?.name ?? "Unknown";
      const conv: Conversation = {
        id: convId,
        vault_id: selectedVaultId,
        vault_name: vaultName,
        title: truncate(trimmed, 60),
        messages: newMessages,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      addConversation(conv);
      setActiveConvId(convId);
      // Sync URL so browser back/forward preserves this conversation.
      // Use replace (not push) to avoid polluting the history stack.
      router.replace(`/copilot?conversation=${convId}`);
      lastLoadedConvRef.current = convId;
    } else {
      updateConversation(convId, newMessages);
    }

    // Cancel previous request if any
    abortRef.current?.abort();
    abortRef.current = new AbortController();

    try {
      // Build conversation history for the backend query rewriter.
      // Send the last 10 turns (20 messages) so the rewriter can
      // resolve pronouns and coreferences like "it", "that invoice", etc.
      const historyForBackend = newMessages
        .slice(-20)
        .filter((m) => m.content.trim())
        .map((m) => ({ role: m.role, content: m.content }));

      const response = await apiFetch<QueryResponse>(
        `/api/vaults/${selectedVaultId}/query`,
        {
          method: "POST",
          body: { query: trimmed, top_k: 5, history: historyForBackend },
          signal: abortRef.current.signal,
        },
      );

      const assistantMsg: Message = {
        id: generateId(),
        role: "assistant",
        content: response.answer,
        citations: response.citations,
        confidence: response.confidence,
        has_sufficient_evidence: response.has_sufficient_evidence,
        chunks_used: response.chunks_used,
        latency_ms: response.latency_ms,
        created_at: new Date().toISOString(),
      };

      // Read from ref again — messages may have changed if the user
      // somehow triggered another update during the await.
      const latest = messagesRef.current;
      const updatedMessages = [...latest, assistantMsg];
      setMessages(updatedMessages);
      updateConversation(convId!, updatedMessages);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      const msg =
        err instanceof Error ? err.message : "Failed to get response";
      setError(msg);
    } finally {
      setIsQuerying(false);
      inputRef.current?.focus();
    }
  }, [
    query,
    selectedVaultId,
    isQuerying,
    activeConvId,
    vaults,
    addConversation,
    updateConversation,
    router,
  ]);

  // ---- Reset helpers ----
  // Shared logic for New Chat + Vault Switch: clears conversation state
  // and navigates to a clean URL.  Uses `pendingResetRef` to prevent
  // the loading effect from re-loading the old conversation while
  // the URL update propagates asynchronously.
  const resetConversation = useCallback(() => {
    // Persist current conversation before clearing
    if (activeConvId && messages.length > 0) {
      updateConversation(activeConvId, messages);
    }

    // Cancel any in-flight query
    abortRef.current?.abort();
    abortRef.current = null;

    setMessages([]);
    setActiveConvId(null);
    setError("");

    // Only need the guard if the URL currently has a conversation param;
    // without one there's no race because the effect won't try to load.
    if (conversationParam) {
      pendingResetRef.current = true;
    } else {
      lastLoadedConvRef.current = null;
    }

    router.replace("/copilot");
  }, [activeConvId, messages, updateConversation, conversationParam, router]);

  // ---- Vault switching ----
  const handleVaultChange = (newVaultId: string) => {
    if (newVaultId === selectedVaultId) return;
    resetConversation();
    setSelectedVaultId(newVaultId);
  };

  // ---- New conversation ----
  const handleNewConversation = () => {
    resetConversation();
  };

  // ---- Keyboard shortcut ----
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const selectedVault = vaults?.find((v) => v.id === selectedVaultId);

  // ---- Derived state: read-only mode ----
  // A conversation becomes read-only when its vault no longer exists.
  // The user can view the full history but cannot send new messages.
  const loadedConv = activeConvId ? getConversation(activeConvId) : null;
  const isReadOnly =
    messages.length > 0 && vaults !== undefined && !selectedVaultId;
  const readOnlyVaultName = loadedConv?.vault_name ?? "Unknown vault";

  // ---- No vaults — only when NOT viewing a conversation ----
  if (
    vaults &&
    vaults.length === 0 &&
    !conversationParam &&
    messages.length === 0
  ) {
    return (
      <EmptyState
        icon={<MessageSquare className="h-10 w-10" />}
        title="No vaults available"
        description="Create a vault and upload documents to start querying."
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
          {isReadOnly ? (
            /* Read-only: show the original vault name with a badge */
            <>
              <div className="flex h-9 items-center gap-2 rounded-lg border border-neutral-200 bg-white px-3 text-sm text-neutral-500 dark:border-neutral-700 dark:bg-neutral-900">
                <FolderLock className="h-3.5 w-3.5" />
                {readOnlyVaultName}
              </div>
              <Badge variant="warning">Vault deleted</Badge>
            </>
          ) : (
            /* Normal: interactive vault selector */
            <>
              <select
                value={selectedVaultId}
                onChange={(e) => handleVaultChange(e.target.value)}
                aria-label="Select vault"
                className="h-9 rounded-lg border border-neutral-200 bg-white px-3 pr-8 text-sm text-foreground outline-none dark:border-neutral-700 dark:bg-neutral-900"
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
            </>
          )}
        </div>
        {(messages.length > 0 || isReadOnly) && (
          <button
            onClick={handleNewConversation}
            className="inline-flex items-center gap-1.5 text-sm font-medium text-neutral-500 hover:text-foreground dark:text-neutral-400"
          >
            <Plus className="h-3.5 w-3.5" />
            New chat
          </button>
        )}
      </div>

      {/* Read-only info banner */}
      {isReadOnly && (
        <div className="mb-3 flex items-center gap-2 rounded-lg bg-amber-50 px-4 py-2.5 text-sm text-amber-700 dark:bg-amber-950/30 dark:text-amber-400">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span>
            This conversation&apos;s vault is no longer available. You can
            review the history but cannot send new messages.
          </span>
        </div>
      )}

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto pr-4">
        {!isHydrated ? (
          /* Conversations may still be loading from localStorage.
             Show a spinner to prevent a flash of the empty-chat state. */
          <div className="flex h-full items-center justify-center">
            <Spinner className="h-5 w-5" />
          </div>
        ) : messages.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <div className="text-center">
              <MessageSquare className="mx-auto h-10 w-10 text-neutral-300 dark:text-neutral-600" />
              <p className="mt-3 text-sm text-neutral-500 dark:text-neutral-400">
                Ask a question about your documents
              </p>
              <p className="mt-1 text-xs text-neutral-400 dark:text-neutral-500">
                Press Enter to send · Shift+Enter for new line
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-6 pb-4">
            {messages.map((msg) => (
              <MessageBubble key={msg.id} message={msg} />
            ))}
            {isQuerying && (
              <div className="flex items-center gap-2 px-1 text-sm text-neutral-500">
                <Spinner className="h-4 w-4" /> Thinking…
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      {/* Error banner */}
      {error && (
        <div className="mb-2 rounded-lg bg-red-50 px-4 py-2.5 text-sm text-red-600 dark:bg-red-950/50 dark:text-red-400">
          {error}
        </div>
      )}

      {/* Input area */}
      <div className="border-t border-neutral-200 pt-4 dark:border-neutral-800">
        <div className="flex gap-3">
          <textarea
            ref={inputRef}
            value={query}
            onChange={handleQueryChange}
            onKeyDown={handleKeyDown}
            aria-label="Message"
            placeholder={
              isReadOnly
                ? "Vault unavailable — start a new chat to continue"
                : selectedVault?.document_count === 0
                  ? "Upload documents to this vault first…"
                  : "Ask a question about your documents…"
            }
            disabled={isReadOnly || selectedVault?.document_count === 0}
            rows={1}
            maxLength={MAX_QUERY_LENGTH}
            className="flex-1 resize-none rounded-lg border border-neutral-200 bg-white px-4 py-2.5 text-sm text-foreground outline-none transition-colors placeholder:text-neutral-400 focus:border-foreground disabled:cursor-not-allowed disabled:opacity-50 dark:border-neutral-700 dark:bg-neutral-900 dark:placeholder:text-neutral-500"
            style={{ minHeight: "40px" }}
          />
          <button
            onClick={handleSend}
            disabled={
              isReadOnly ||
              !query.trim() ||
              isQuerying ||
              !selectedVaultId ||
              selectedVault?.document_count === 0
            }
            aria-label="Send message"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-foreground text-background transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isQuerying ? (
              <Spinner className="h-4 w-4 text-background" />
            ) : (
              <Send className="h-4 w-4" />
            )}
          </button>
        </div>
        {query.length > MAX_QUERY_LENGTH - 200 && (
          <p
            className={cn(
              "mt-1 text-right text-xs",
              query.length >= MAX_QUERY_LENGTH
                ? "text-red-500"
                : "text-neutral-400",
            )}
          >
            {query.length}/{MAX_QUERY_LENGTH}
          </p>
        )}
      </div>
    </div>
  );
}
