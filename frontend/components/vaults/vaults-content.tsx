"use client";

import { useState } from "react";
import useSWR from "swr";
import { Plus, FolderLock } from "lucide-react";
import { apiFetch, ApiError, fetcher } from "@/lib/api";
import type { Vault } from "@/lib/types";
import { Spinner } from "@/components/ui/spinner";
import { EmptyState } from "@/components/ui/empty-state";
import { Modal } from "@/components/ui/modal";
import { VaultCard } from "./vault-card";
import { VaultCreateModal } from "./vault-create-modal";

export default function VaultsContent() {
  const { data: vaults, isLoading, mutate } = useSWR<Vault[]>("/api/vaults", fetcher);

  const [showCreate, setShowCreate] = useState(false);

  // Edit state
  const [editVault, setEditVault] = useState<Vault | null>(null);
  const [editName, setEditName] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [editSaving, setEditSaving] = useState(false);
  const [editError, setEditError] = useState("");

  // Delete state
  const [deleteVault, setDeleteVault] = useState<Vault | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");

  // ---------- Edit handlers ----------

  function openEdit(vault: Vault) {
    setEditVault(vault);
    setEditName(vault.name);
    setEditDesc(vault.description ?? "");
    setEditError("");
  }

  function closeEdit() {
    setEditVault(null);
    setEditError("");
    setEditSaving(false);
  }

  async function handleEditSave(e: React.FormEvent) {
    e.preventDefault();
    if (!editVault) return;

    const trimmed = editName.trim();
    if (!trimmed) {
      setEditError("Name is required");
      return;
    }

    setEditSaving(true);
    setEditError("");

    try {
      await apiFetch(`/api/vaults/${editVault.id}`, {
        method: "PATCH",
        body: {
          name: trimmed,
          description: editDesc.trim() || null,
        },
      });
      await mutate();
      closeEdit();
    } catch (err) {
      setEditError(err instanceof ApiError ? err.detail : "Failed to update vault");
    } finally {
      setEditSaving(false);
    }
  }

  // ---------- Delete handlers ----------

  function openDelete(vault: Vault) {
    setDeleteVault(vault);
    setDeleteError("");
  }

  function closeDelete() {
    setDeleteVault(null);
    setDeleteError("");
    setDeleting(false);
  }

  async function handleDelete() {
    if (!deleteVault || deleting) return;

    setDeleting(true);
    setDeleteError("");

    try {
      await apiFetch(`/api/vaults/${deleteVault.id}`, { method: "DELETE" });
      await mutate();
      closeDelete();
    } catch (err) {
      setDeleteError(err instanceof ApiError ? err.detail : "Failed to delete vault");
    } finally {
      setDeleting(false);
    }
  }

  // ---------- Render ----------

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner className="h-6 w-6" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Vaults</h2>
          <p className="mt-0.5 text-sm text-neutral-500 dark:text-neutral-400">
            Manage your document collections
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="inline-flex h-9 items-center gap-2 rounded-lg bg-foreground px-4 text-sm font-medium text-background transition-colors hover:opacity-90"
        >
          <Plus className="h-4 w-4" />
          New Vault
        </button>
      </div>

      {/* Vault grid */}
      {!vaults || vaults.length === 0 ? (
        <EmptyState
          icon={<FolderLock className="h-10 w-10" />}
          title="No vaults yet"
          description="Create your first vault to start uploading and querying documents."
          action={
            <button
              onClick={() => setShowCreate(true)}
              className="inline-flex h-9 items-center gap-2 rounded-lg bg-foreground px-4 text-sm font-medium text-background transition-colors hover:opacity-90"
            >
              <Plus className="h-4 w-4" />
              Create Vault
            </button>
          }
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {vaults.map((vault) => (
            <VaultCard
              key={vault.id}
              vault={vault}
              onEdit={openEdit}
              onDelete={openDelete}
            />
          ))}
        </div>
      )}

      {/* Create modal */}
      <VaultCreateModal
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onCreated={() => mutate()}
      />

      {/* Edit modal */}
      <Modal
        open={!!editVault}
        onClose={closeEdit}
        title="Edit Vault"
      >
        <form onSubmit={handleEditSave} className="space-y-4">
          <div>
            <label className="mb-1.5 block text-sm font-medium text-foreground">
              Name
            </label>
            <input
              type="text"
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              autoFocus
              className="h-11 w-full rounded-lg border border-neutral-200 bg-white px-3.5 text-sm text-foreground outline-none transition-colors placeholder:text-neutral-400 focus:border-foreground dark:border-neutral-800 dark:bg-neutral-950"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-foreground">
              Description
            </label>
            <textarea
              value={editDesc}
              onChange={(e) => setEditDesc(e.target.value)}
              rows={3}
              className="w-full resize-none rounded-lg border border-neutral-200 bg-white px-3.5 py-2.5 text-sm text-foreground outline-none transition-colors placeholder:text-neutral-400 focus:border-foreground dark:border-neutral-800 dark:bg-neutral-950"
            />
          </div>

          {editError && (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600 dark:bg-red-950/50 dark:text-red-400">
              {editError}
            </p>
          )}

          <div className="flex justify-end gap-3">
            <button
              type="button"
              onClick={closeEdit}
              className="rounded-lg px-4 py-2 text-sm text-neutral-600 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-800"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={editSaving || !editName.trim()}
              className="rounded-lg bg-foreground px-4 py-2 text-sm font-medium text-background transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {editSaving ? "Saving…" : "Save Changes"}
            </button>
          </div>
        </form>
      </Modal>

      {/* Delete confirmation modal */}
      <Modal
        open={!!deleteVault}
        onClose={closeDelete}
        title="Delete Vault"
      >
        <p className="text-sm text-neutral-500 dark:text-neutral-400">
          Are you sure you want to delete{" "}
          <span className="font-medium text-foreground">
            {deleteVault?.name}
          </span>
          ? All documents in this vault will be permanently removed. This action
          cannot be undone.
        </p>

        {deleteError && (
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600 dark:bg-red-950/50 dark:text-red-400">
            {deleteError}
          </p>
        )}

        <div className="mt-4 flex justify-end gap-3">
          <button
            onClick={closeDelete}
            className="rounded-lg px-4 py-2 text-sm text-neutral-600 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-800"
          >
            Cancel
          </button>
          <button
            onClick={handleDelete}
            disabled={deleting}
            className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {deleting ? "Deleting…" : "Delete Vault"}
          </button>
        </div>
      </Modal>
    </div>
  );
}
