"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowLeft,
  FileText,
  Trash2,
  AlertCircle,
  Eye,
  X,
  Search,
} from "lucide-react";
import { apiFetch, ApiError, fetcher } from "@/lib/api";
import { formatFileSize, formatRelativeTime } from "@/lib/utils";
import { DOCUMENT_POLL_INTERVAL_MS } from "@/lib/constants";
import type { Vault, Document } from "@/lib/types";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { EmptyState } from "@/components/ui/empty-state";
import { Modal } from "@/components/ui/modal";
import { Badge } from "@/components/ui/badge";
import { Pagination } from "@/components/ui/pagination";
import { DocumentStatusBadge } from "./document-status-badge";
import { DocumentUploadZone } from "./document-upload-zone";

type Props = {
  vaultId: string;
};

export default function VaultDetailContent({ vaultId }: Props) {
  const router = useRouter();

  // ---- Vault data ----
  const {
    data: vault,
    isLoading: vaultLoading,
    error: vaultError,
    mutate: mutateVault,
  } = useSWR<Vault>(`/api/vaults/${vaultId}`, fetcher);

  // ---- Documents with server-side pagination + search ----
  const DOCS_PER_PAGE = 20;
  const [docSearch, setDocSearch] = useState("");
  const [docPage, setDocPage] = useState(1);
  const [pollInterval, setPollInterval] = useState(0);

  // Debounce search to avoid hammering the server on every keystroke
  const [debouncedSearch, setDebouncedSearch] = useState("");
  useEffect(() => {
    const id = setTimeout(() => setDebouncedSearch(docSearch), 300);
    return () => clearTimeout(id);
  }, [docSearch]);

  // Build SWR key with server-side pagination & search params
  const docsKey = useMemo(() => {
    const params = new URLSearchParams({
      skip: String((docPage - 1) * DOCS_PER_PAGE),
      limit: String(DOCS_PER_PAGE),
    });
    if (debouncedSearch) params.set("search", debouncedSearch);
    return `/api/vaults/${vaultId}/documents?${params}`;
  }, [vaultId, docPage, debouncedSearch]);

  // Custom fetcher that returns both the document array and total count
  const docsFetcher = useCallback(async (url: string) => {
    const res = await fetch(url);
    if (!res.ok) throw new Error("Failed to fetch documents");
    const docs: Document[] = await res.json();
    const total = parseInt(res.headers.get("X-Total-Count") ?? "0", 10);
    return { docs, total };
  }, []);

  const {
    data: docsData,
    isLoading: docsLoading,
    isValidating: docsValidating,
    mutate: mutateDocs,
  } = useSWR<{ docs: Document[]; total: number }>(
    docsKey,
    docsFetcher,
    { refreshInterval: pollInterval, keepPreviousData: true },
  );

  const documents = docsData?.docs;
  const docTotal = docsData?.total ?? 0;
  const docTotalPages = Math.max(1, Math.ceil(docTotal / DOCS_PER_PAGE));

  // Enable polling when documents are processing
  useEffect(() => {
    const hasPending = documents?.some(
      (d) => d.status === "pending" || d.status === "ingesting",
    );
    setPollInterval(hasPending ? DOCUMENT_POLL_INTERVAL_MS : 0);
  }, [documents]);

  // Reset to page 1 when search changes
  useEffect(() => {
    setDocPage(1);
  }, [debouncedSearch]);

  // If a deletion empties the current page, step back
  useEffect(() => {
    if (docsData && docsData.docs.length === 0 && docPage > 1) {
      setDocPage((p) => p - 1);
    }
  }, [docsData, docPage]);

  // ---- Delete document ----
  const [deleteDoc, setDeleteDoc] = useState<Document | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");

  // ---- Document preview ----
  const [previewDoc, setPreviewDoc] = useState<Document | null>(null);
  const [previewMarkdown, setPreviewMarkdown] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");

  async function handleDeleteDoc() {
    if (!deleteDoc || deleting) return;
    setDeleting(true);
    setDeleteError("");

    try {
      await apiFetch(`/api/vaults/${vaultId}/documents/${deleteDoc.id}`, {
        method: "DELETE",
      });
      await mutateDocs();
      await mutateVault();
      setDeleteDoc(null);
    } catch (err) {
      setDeleteError(
        err instanceof ApiError ? err.detail : "Failed to delete document",
      );
    } finally {
      setDeleting(false);
    }
  }

  const onUploadComplete = useCallback(() => {
    mutateDocs();
    mutateVault();
  }, [mutateDocs, mutateVault]);

  // ---- Open document preview ----
  const openPreview = useCallback(
    async (doc: Document) => {
      if (doc.status !== "active") return; // Only preview active docs
      setPreviewDoc(doc);
      setPreviewMarkdown("");
      setPreviewError("");
      setPreviewLoading(true);

      try {
        const data = await apiFetch<{
          markdown: string;
          char_count: number;
        }>(`/api/vaults/${vaultId}/documents/${doc.id}/content`);
        setPreviewMarkdown(data.markdown);
      } catch (err) {
        const message =
          err instanceof ApiError ? err.detail : "Failed to load document";
        setPreviewError(message);
      } finally {
        setPreviewLoading(false);
      }
    },
    [vaultId],
  );

  function closePreview() {
    setPreviewDoc(null);
    setPreviewMarkdown("");
    setPreviewError("");
  }

  // ---- Escape key closes preview overlay ----
  useEffect(() => {
    if (!previewDoc) return;
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") closePreview();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [previewDoc]);

  // ---- Loading / Error ----
  // Full-page spinner ONLY on very first mount (no data at all).
  // During pagination / search revalidation keepPreviousData keeps
  // stale data visible so the tree is never unmounted.
  if (vaultLoading || (docsLoading && !docsData)) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner className="h-6 w-6" />
      </div>
    );
  }

  if (vaultError || !vault) {
    return (
      <div className="flex h-64 flex-col items-center justify-center text-center">
        <AlertCircle className="h-8 w-8 text-neutral-400" />
        <p className="mt-3 text-sm text-neutral-500 dark:text-neutral-400">
          Vault not found or you don&apos;t have access.
        </p>
        <button
          onClick={() => router.push("/vaults")}
          className="mt-4 text-sm font-medium text-foreground hover:underline"
        >
          ← Back to Vaults
        </button>
      </div>
    );
  }

  const canEdit = vault.role === "owner" || vault.role === "editor";

  return (
    <div className="space-y-6">
      {/* Back + header */}
      <div>
        <button
          onClick={() => router.push("/vaults")}
          className="mb-4 inline-flex items-center gap-1.5 text-sm text-neutral-500 hover:text-foreground dark:text-neutral-400"
        >
          <ArrowLeft className="h-4 w-4" />
          Vaults
        </button>

        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-lg font-semibold text-foreground">
              {vault.name}
            </h2>
            {vault.description && (
              <p className="mt-1 text-sm text-neutral-500 dark:text-neutral-400">
                {vault.description}
              </p>
            )}
            <div className="mt-2 flex items-center gap-3 text-xs text-neutral-400">
              <Badge variant="neutral">{vault.role}</Badge>
              <span>
                {vault.document_count} document
                {vault.document_count !== 1 ? "s" : ""}
              </span>
              <span>Updated {formatRelativeTime(vault.updated_at)}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Upload zone — editor+ only */}
      {canEdit && (
        <DocumentUploadZone
          vaultId={vaultId}
          onUploadComplete={onUploadComplete}
        />
      )}

      {/* Document table */}
      {docTotal === 0 && !debouncedSearch ? (
        <EmptyState
          icon={<FileText className="h-10 w-10" />}
          title="No documents yet"
          description={
            canEdit
              ? "Upload documents above to start building your knowledge base."
              : "No documents have been uploaded to this vault yet."
          }
        />
      ) : (
        <div>
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-foreground">
                Documents{" "}
                <span className="font-normal text-neutral-400">
                  ({docTotal})
                </span>
                {docsValidating && (
                  <Spinner className="ml-2 inline-block h-3 w-3 align-middle" />
                )}
              </h3>
              <div className="relative w-56">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
                <input
                  type="text"
                  value={docSearch}
                  onChange={(e) => setDocSearch(e.target.value)}
                  placeholder="Search documents…"
                  aria-label="Search documents"
                  className="h-8 w-full rounded-lg border border-neutral-200 bg-white pl-9 pr-3 text-sm text-foreground outline-none placeholder:text-neutral-400 focus:border-foreground dark:border-neutral-700 dark:bg-neutral-900 dark:placeholder:text-neutral-500"
                />
              </div>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            {docTotal === 0 ? (
              <p className="px-5 py-8 text-center text-sm text-neutral-500 dark:text-neutral-400">
                No documents match &ldquo;{docSearch}&rdquo;
              </p>
            ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-neutral-100 text-left dark:border-neutral-800">
                    <th className="px-5 py-3 text-xs font-medium uppercase tracking-wider text-neutral-500">
                      Name
                    </th>
                    <th className="hidden px-5 py-3 text-xs font-medium uppercase tracking-wider text-neutral-500 sm:table-cell">
                      Type
                    </th>
                    <th className="hidden px-5 py-3 text-xs font-medium uppercase tracking-wider text-neutral-500 sm:table-cell">
                      Size
                    </th>
                    <th className="px-5 py-3 text-xs font-medium uppercase tracking-wider text-neutral-500">
                      Status
                    </th>
                    <th className="hidden px-5 py-3 text-xs font-medium uppercase tracking-wider text-neutral-500 md:table-cell">
                      Added
                    </th>
                    {canEdit && (
                      <th className="px-5 py-3 text-xs font-medium uppercase tracking-wider text-neutral-500">
                        <span className="sr-only">Actions</span>
                      </th>
                    )}
                  </tr>
                </thead>
                <tbody className="divide-y divide-neutral-100 dark:divide-neutral-800">
                  {(documents ?? []).map((doc) => (
                    <tr
                      key={doc.id}
                      className="transition-colors hover:bg-neutral-50 dark:hover:bg-neutral-800/20"
                    >
                      <td className="px-5 py-3">
                        <div className="flex items-center gap-2">
                          <FileText className="h-4 w-4 shrink-0 text-neutral-400" />
                          {doc.status === "active" ? (
                            <button
                              onClick={() => openPreview(doc)}
                              className="max-w-[200px] truncate font-medium text-foreground underline decoration-neutral-300 underline-offset-2 transition-colors hover:decoration-foreground dark:decoration-neutral-600"
                              title="View document content"
                            >
                              {doc.original_filename}
                            </button>
                          ) : (
                            <span className="max-w-[200px] truncate font-medium text-foreground">
                              {doc.original_filename}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="hidden px-5 py-3 uppercase text-neutral-500 sm:table-cell">
                        {doc.file_type}
                      </td>
                      <td className="hidden px-5 py-3 text-neutral-500 sm:table-cell">
                        {formatFileSize(doc.file_size_bytes)}
                      </td>
                      <td className="px-5 py-3">
                        <DocumentStatusBadge status={doc.status} />
                        {doc.status === "failed" && doc.error_message && (
                          <p className="mt-1 max-w-[200px] truncate text-xs text-red-500">
                            {doc.error_message}
                          </p>
                        )}
                      </td>
                      <td className="hidden px-5 py-3 text-neutral-500 md:table-cell">
                        {formatRelativeTime(doc.created_at)}
                      </td>
                      {canEdit && (
                        <td className="px-5 py-3 text-right">
                          <div className="flex items-center justify-end gap-2">
                            {doc.status === "active" && (
                              <button
                                onClick={() => openPreview(doc)}
                                className="text-neutral-300 hover:text-foreground dark:text-neutral-600 dark:hover:text-neutral-300"
                                title="View document"
                              >
                                <Eye className="h-4 w-4" />
                              </button>
                            )}
                            <button
                              onClick={() => setDeleteDoc(doc)}
                              className="text-neutral-300 hover:text-red-500 dark:text-neutral-600 dark:hover:text-red-400"
                              title="Delete document"
                            >
                              <Trash2 className="h-4 w-4" />
                            </button>
                          </div>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            )}
            <Pagination
              page={docPage}
              totalPages={docTotalPages}
              onPageChange={setDocPage}
              className="pb-4"
            />
          </CardContent>
        </Card>
        </div>
      )}

      {/* Delete document confirmation */}
      <Modal
        open={!!deleteDoc}
        onClose={() => {
          setDeleteDoc(null);
          setDeleteError("");
        }}
        title="Delete Document"
      >
        <p className="text-sm text-neutral-500 dark:text-neutral-400">
          Are you sure you want to delete{" "}
          <span className="font-medium text-foreground">
            {deleteDoc?.original_filename}
          </span>
          ? This action cannot be undone.
        </p>

        {deleteError && (
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600 dark:bg-red-950/50 dark:text-red-400">
            {deleteError}
          </p>
        )}

        <div className="mt-4 flex justify-end gap-3">
          <button
            onClick={() => {
              setDeleteDoc(null);
              setDeleteError("");
            }}
            className="rounded-lg px-4 py-2 text-sm text-neutral-600 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-800"
          >
            Cancel
          </button>
          <button
            onClick={handleDeleteDoc}
            disabled={deleting}
            className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {deleting ? "Deleting…" : "Delete"}
          </button>
        </div>
      </Modal>

      {/* Document preview — full-screen overlay for comfortable reading */}
      {previewDoc && (
        <div
          className="fixed inset-0 z-50 flex flex-col bg-white dark:bg-[#1a1a1a]"
          role="dialog"
          aria-modal="true"
          aria-label={`Preview: ${previewDoc.original_filename}`}
        >
          {/* Preview header */}
          <div className="flex items-center justify-between border-b border-neutral-200 px-6 py-3 dark:border-neutral-800">
            <div className="flex items-center gap-3 overflow-hidden">
              <FileText className="h-5 w-5 shrink-0 text-neutral-400" />
              <div className="min-w-0">
                <h3 className="truncate text-sm font-semibold text-foreground">
                  {previewDoc.original_filename}
                </h3>
                <p className="text-xs text-neutral-400">
                  {previewDoc.file_type.toUpperCase()}
                  {previewDoc.file_size_bytes
                    ? ` · ${formatFileSize(previewDoc.file_size_bytes)}`
                    : ""}
                </p>
              </div>
            </div>
            <button
              onClick={closePreview}
              aria-label="Close preview"
              className="rounded-md p-1.5 text-neutral-500 transition-colors hover:bg-neutral-100 hover:text-foreground dark:hover:bg-neutral-800"
            >
              <X className="h-5 w-5" />
            </button>
          </div>

          {/* Preview body */}
          <div className="flex-1 overflow-y-auto px-6 py-6 sm:px-12 md:px-20">
            {previewLoading && (
              <div className="flex h-64 items-center justify-center">
                <Spinner className="h-6 w-6" />
              </div>
            )}

            {previewError && (
              <div className="flex h-64 flex-col items-center justify-center text-center">
                <AlertCircle className="h-8 w-8 text-neutral-400" />
                <p className="mt-3 text-sm text-red-500">{previewError}</p>
                <button
                  onClick={closePreview}
                  className="mt-4 text-sm font-medium text-foreground hover:underline"
                >
                  Close
                </button>
              </div>
            )}

            {!previewLoading && !previewError && previewMarkdown && (
              <article className="prose prose-sm mx-auto max-w-3xl text-foreground dark:prose-invert prose-headings:text-foreground prose-p:my-2 prose-table:my-2 prose-th:px-3 prose-th:py-2 prose-td:px-3 prose-td:py-2 prose-table:border prose-th:border prose-td:border prose-th:bg-neutral-50 prose-pre:bg-neutral-100 dark:prose-th:bg-neutral-800/50 dark:prose-pre:bg-neutral-800">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {previewMarkdown}
                </ReactMarkdown>
              </article>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
