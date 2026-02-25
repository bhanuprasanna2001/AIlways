"use client";

import { useState, useMemo } from "react";
import { useRouter } from "next/navigation";
import { Clock, Trash2, Search } from "lucide-react";
import { EmptyState } from "@/components/ui/empty-state";
import { Modal } from "@/components/ui/modal";
import { useConversations } from "@/hooks/use-conversations";
import { formatRelativeTime, truncate } from "@/lib/utils";

export default function HistoryContent() {
  const router = useRouter();
  const { conversations, removeConversation, clearAll } = useConversations();
  const [search, setSearch] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [showClearAll, setShowClearAll] = useState(false);

  const filtered = useMemo(() => {
    if (!search) return conversations;
    const term = search.toLowerCase();
    return conversations.filter(
      (c) =>
        c.title.toLowerCase().includes(term) ||
        c.vault_name.toLowerCase().includes(term),
    );
  }, [conversations, search]);

  // ---- Empty state ----
  if (conversations.length === 0) {
    return (
      <EmptyState
        icon={<Clock className="h-10 w-10" />}
        title="No conversations yet"
        description="Start a conversation in Copilot to see your history here."
        action={
          <a
            href="/copilot"
            className="inline-flex h-9 items-center rounded-lg bg-foreground px-4 text-sm font-medium text-background transition-colors hover:opacity-90"
          >
            Open Copilot
          </a>
        }
      />
    );
  }

  return (
    <div className="space-y-4">
      {/* Search + clear */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search conversations…"
            aria-label="Search conversations"
            className="h-9 w-full rounded-lg border border-neutral-200 bg-white pl-9 pr-3.5 text-sm text-foreground outline-none placeholder:text-neutral-400 focus:border-foreground dark:border-neutral-700 dark:bg-neutral-900 dark:placeholder:text-neutral-500"
          />
        </div>
        <button
          onClick={() => setShowClearAll(true)}
          className="shrink-0 text-sm text-neutral-400 hover:text-red-500 dark:hover:text-red-400"
        >
          Clear all
        </button>
      </div>

      {/* Conversation list */}
      {filtered.length === 0 ? (
        <p className="py-8 text-center text-sm text-neutral-500 dark:text-neutral-400">
          No conversations match your search
        </p>
      ) : (
        <div className="space-y-2">
          {filtered.map((conv) => (
            <div
              key={conv.id}
              className="group flex items-center justify-between rounded-xl border border-neutral-200 px-4 py-3 transition-colors hover:bg-neutral-50 dark:border-neutral-800 dark:hover:bg-neutral-800/30"
            >
              <button
                onClick={() =>
                  router.push(`/copilot?conversation=${conv.id}`)
                }
                className="flex-1 text-left"
              >
                <p className="text-sm font-medium text-foreground">
                  {truncate(conv.title, 80)}
                </p>
                <p className="mt-0.5 text-xs text-neutral-500 dark:text-neutral-400">
                  {conv.vault_name} · {conv.messages.length} message
                  {conv.messages.length !== 1 ? "s" : ""} ·{" "}
                  {formatRelativeTime(conv.updated_at)}
                </p>
              </button>
              <button
                onClick={() => setDeleteTarget(conv.id)}
                className="ml-4 text-neutral-300 opacity-0 transition-opacity hover:text-red-500 group-hover:opacity-100 group-focus-within:opacity-100 dark:text-neutral-600 dark:hover:text-red-400"
                title="Delete conversation"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Delete single confirmation */}
      <Modal
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        title="Delete conversation"
      >
        <p className="text-sm text-neutral-500 dark:text-neutral-400">
          This conversation will be permanently deleted. This action cannot be
          undone.
        </p>
        <div className="mt-4 flex justify-end gap-3">
          <button
            onClick={() => setDeleteTarget(null)}
            className="rounded-lg px-4 py-2 text-sm text-neutral-600 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-800"
          >
            Cancel
          </button>
          <button
            onClick={() => {
              if (deleteTarget) removeConversation(deleteTarget);
              setDeleteTarget(null);
            }}
            className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700"
          >
            Delete
          </button>
        </div>
      </Modal>

      {/* Clear all confirmation */}
      <Modal
        open={showClearAll}
        onClose={() => setShowClearAll(false)}
        title="Clear all history"
      >
        <p className="text-sm text-neutral-500 dark:text-neutral-400">
          All conversation history will be permanently deleted. This action
          cannot be undone.
        </p>
        <div className="mt-4 flex justify-end gap-3">
          <button
            onClick={() => setShowClearAll(false)}
            className="rounded-lg px-4 py-2 text-sm text-neutral-600 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-800"
          >
            Cancel
          </button>
          <button
            onClick={() => {
              clearAll();
              setShowClearAll(false);
            }}
            className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700"
          >
            Clear All
          </button>
        </div>
      </Modal>
    </div>
  );
}
