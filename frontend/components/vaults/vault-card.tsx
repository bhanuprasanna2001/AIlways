"use client";

import Link from "next/link";
import { FolderLock, ArrowRight, MoreVertical, Pencil, Trash2 } from "lucide-react";
import { useState, useRef, useEffect } from "react";
import type { Vault } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { formatRelativeTime } from "@/lib/utils";

type Props = {
  vault: Vault;
  onEdit: (vault: Vault) => void;
  onDelete: (vault: Vault) => void;
};

export function VaultCard({ vault, onEdit, onDelete }: Props) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    if (menuOpen) {
      document.addEventListener("mousedown", handleClick);
    }
    return () => document.removeEventListener("mousedown", handleClick);
  }, [menuOpen]);

  const canManage = vault.role === "owner" || vault.role === "editor";

  return (
    <div className="group relative rounded-xl border border-neutral-200 bg-white p-5 transition-colors hover:border-neutral-300 dark:border-neutral-800 dark:bg-white/[0.03] dark:hover:border-neutral-700">
      {/* Context menu — only for owners/editors, absolute top-right */}
      {canManage && (
        <div ref={menuRef} className="absolute right-3 top-3 z-10">
          <button
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setMenuOpen(!menuOpen);
            }}
            className="rounded-md p-1 text-neutral-300 opacity-0 transition-opacity hover:text-neutral-500 group-hover:opacity-100 group-focus-within:opacity-100 dark:text-neutral-600 dark:hover:text-neutral-400"
          >
            <MoreVertical className="h-4 w-4" />
          </button>

          {menuOpen && (
            <div className="absolute right-0 z-20 mt-1 w-36 rounded-lg border border-neutral-200 bg-white py-1 shadow-lg dark:border-neutral-700 dark:bg-neutral-900">
              <button
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setMenuOpen(false);
                  onEdit(vault);
                }}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-sm text-neutral-600 hover:bg-neutral-50 dark:text-neutral-300 dark:hover:bg-neutral-800"
              >
                <Pencil className="h-3.5 w-3.5" />
                Edit
              </button>
              {vault.role === "owner" && (
                <button
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setMenuOpen(false);
                    onDelete(vault);
                  }}
                  className="flex w-full items-center gap-2 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/50"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Delete
                </button>
              )}
            </div>
          )}
        </div>
      )}

      <Link href={`/vaults/${vault.id}`} className="block">
        {/* Pad right to prevent text from going under the menu button */}
        <div className={`flex items-start gap-4 ${canManage ? "pr-6" : ""}`}>
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-neutral-100 dark:bg-neutral-800">
            <FolderLock className="h-5 w-5 text-neutral-500 dark:text-neutral-400" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h3 className="truncate text-sm font-semibold text-foreground">
                {vault.name}
              </h3>
              <Badge variant="neutral">{vault.role}</Badge>
            </div>
            {vault.description && (
              <p className="mt-1 line-clamp-2 text-xs text-neutral-500 dark:text-neutral-400">
                {vault.description}
              </p>
            )}
            {/* Meta row — arrow at the bottom, separated from three-dot */}
            <div className="mt-3 flex items-center justify-between">
              <div className="flex items-center gap-3 text-xs text-neutral-400">
                <span>
                  {vault.document_count} doc
                  {vault.document_count !== 1 ? "s" : ""}
                </span>
                <span>·</span>
                <span>{formatRelativeTime(vault.updated_at)}</span>
              </div>
              <ArrowRight className="h-5 w-5 shrink-0 text-neutral-300 transition-transform group-hover:translate-x-0.5 dark:text-neutral-600" />
            </div>
          </div>
        </div>
      </Link>
    </div>
  );
}
