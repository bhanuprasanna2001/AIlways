"use client";

import { useState } from "react";
import { apiFetch, ApiError } from "@/lib/api";
import { Modal } from "@/components/ui/modal";

type Props = {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
};

export function VaultCreateModal({ open, onClose, onCreated }: Props) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  function reset() {
    setName("");
    setDescription("");
    setError("");
    setSaving(false);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName) {
      setError("Name is required");
      return;
    }

    setSaving(true);
    setError("");

    try {
      await apiFetch("/api/vaults", {
        method: "POST",
        body: {
          name: trimmedName,
          description: description.trim() || null,
        },
      });
      reset();
      onCreated();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Failed to create vault");
    } finally {
      setSaving(false);
    }
  }

  function handleClose() {
    reset();
    onClose();
  }

  return (
    <Modal open={open} onClose={handleClose} title="Create Vault">
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="mb-1.5 block text-sm font-medium text-foreground">
            Name
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Financial Documents"
            autoFocus
            className="h-11 w-full rounded-lg border border-neutral-200 bg-white px-3.5 text-sm text-foreground outline-none transition-colors placeholder:text-neutral-400 focus:border-foreground dark:border-neutral-800 dark:bg-neutral-950 dark:placeholder:text-neutral-500"
          />
        </div>

        <div>
          <label className="mb-1.5 block text-sm font-medium text-foreground">
            Description{" "}
            <span className="font-normal text-neutral-400">(optional)</span>
          </label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            placeholder="What kind of documents will this vault hold?"
            className="w-full resize-none rounded-lg border border-neutral-200 bg-white px-3.5 py-2.5 text-sm text-foreground outline-none transition-colors placeholder:text-neutral-400 focus:border-foreground dark:border-neutral-800 dark:bg-neutral-950 dark:placeholder:text-neutral-500"
          />
        </div>

        {error && (
          <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600 dark:bg-red-950/50 dark:text-red-400">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-3">
          <button
            type="button"
            onClick={handleClose}
            className="rounded-lg px-4 py-2 text-sm text-neutral-600 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-800"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={saving || !name.trim()}
            className="rounded-lg bg-foreground px-4 py-2 text-sm font-medium text-background transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving ? "Creating…" : "Create Vault"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
