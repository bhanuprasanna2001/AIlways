"use client";

import useSWR from "swr";
import Link from "next/link";
import {
  FolderLock,
  FileText,
  TrendingUp,
  Clock,
  ArrowRight,
  Plus,
} from "lucide-react";
import { fetcher } from "@/lib/api";
import { formatRelativeTime } from "@/lib/utils";
import type { Vault } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { EmptyState } from "@/components/ui/empty-state";
import { Badge } from "@/components/ui/badge";

// ---------------------------------------------------------------------------
// Metric Card
// ---------------------------------------------------------------------------

function MetricCard({
  icon: Icon,
  label,
  value,
  sub,
}: {
  icon: React.ElementType;
  label: string;
  value: string | number;
  sub?: string;
}) {
  return (
    <Card>
      <CardContent className="py-5">
        <div className="flex items-start justify-between">
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-neutral-500 dark:text-neutral-400">
              {label}
            </p>
            <p className="mt-2 text-2xl font-bold text-foreground">{value}</p>
            {sub && (
              <p className="mt-0.5 text-xs text-neutral-400 dark:text-neutral-500">
                {sub}
              </p>
            )}
          </div>
          <div className="rounded-lg bg-neutral-100 p-2.5 dark:bg-neutral-800">
            <Icon className="h-5 w-5 text-neutral-500 dark:text-neutral-400" />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Vault Row
// ---------------------------------------------------------------------------

function VaultRow({ vault }: { vault: Vault }) {
  return (
    <Link
      href={`/vaults/${vault.id}`}
      className="group flex items-center justify-between rounded-xl border border-neutral-200 px-5 py-4 transition-colors hover:bg-neutral-50 dark:border-neutral-800 dark:hover:bg-neutral-800/30"
    >
      <div className="flex items-center gap-4">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-neutral-100 dark:bg-neutral-800">
          <FolderLock className="h-5 w-5 text-neutral-500 dark:text-neutral-400" />
        </div>
        <div>
          <p className="text-sm font-semibold text-foreground">{vault.name}</p>
          {vault.description && (
            <p className="mt-0.5 line-clamp-1 text-xs text-neutral-500 dark:text-neutral-400">
              {vault.description}
            </p>
          )}
        </div>
      </div>
      <div className="flex items-center gap-4">
        <div className="hidden items-center gap-4 sm:flex">
          <Badge variant="neutral">{vault.role}</Badge>
          <span className="text-xs tabular-nums text-neutral-400">
            {vault.document_count} doc{vault.document_count !== 1 ? "s" : ""}
          </span>
          <span className="text-xs text-neutral-400">
            {formatRelativeTime(vault.updated_at)}
          </span>
        </div>
        <ArrowRight className="h-4 w-4 text-neutral-300 transition-transform group-hover:translate-x-0.5 dark:text-neutral-600" />
      </div>
    </Link>
  );
}

// ---------------------------------------------------------------------------
// Dashboard Content
// ---------------------------------------------------------------------------

export default function DashboardContent() {
  const { data: vaults, isLoading, error } = useSWR<Vault[]>("/api/vaults", fetcher);

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner className="h-6 w-6" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-64 flex-col items-center justify-center text-center">
        <p className="text-sm text-red-500">
          {error instanceof Error ? error.message : "Failed to load dashboard data"}
        </p>
        <p className="mt-1 text-xs text-neutral-400">Try refreshing the page</p>
      </div>
    );
  }

  // ---------- Compute metrics ----------
  const totalVaults = vaults?.length ?? 0;
  const totalDocs =
    vaults?.reduce((sum, v) => sum + v.document_count, 0) ?? 0;

  const largestVault = vaults?.length
    ? vaults.reduce((a, b) =>
        a.document_count >= b.document_count ? a : b,
      )
    : null;

  const latestVault = vaults?.length
    ? vaults.reduce((a, b) =>
        new Date(a.updated_at) >= new Date(b.updated_at) ? a : b,
      )
    : null;

  // ---------- Empty state ----------
  if (!vaults || vaults.length === 0) {
    return (
      <EmptyState
        icon={<FolderLock className="h-10 w-10" />}
        title="No vaults yet"
        description="Create your first vault to start uploading and querying documents."
        action={
          <Link
            href="/vaults"
            className="inline-flex h-9 items-center gap-2 rounded-lg bg-foreground px-4 text-sm font-medium text-background transition-colors hover:opacity-90"
          >
            <Plus className="h-4 w-4" />
            Create Vault
          </Link>
        }
      />
    );
  }

  return (
    <div className="space-y-8">
      {/* Metrics Grid */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard
          icon={FolderLock}
          label="Total Vaults"
          value={totalVaults}
        />
        <MetricCard
          icon={FileText}
          label="Total Documents"
          value={totalDocs}
        />
        <MetricCard
          icon={TrendingUp}
          label="Largest Vault"
          value={largestVault?.name ?? "—"}
          sub={
            largestVault
              ? `${largestVault.document_count} documents`
              : undefined
          }
        />
        <MetricCard
          icon={Clock}
          label="Latest Activity"
          value={latestVault ? formatRelativeTime(latestVault.updated_at) : "—"}
          sub={latestVault?.name}
        />
      </div>

      {/* Vault list */}
      <div>
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-foreground">Your Vaults</h3>
          <Link
            href="/vaults"
            className="text-xs font-medium text-neutral-500 hover:text-foreground dark:text-neutral-400"
          >
            View all →
          </Link>
        </div>
        <div className="space-y-2">
          {vaults.map((vault) => (
            <VaultRow key={vault.id} vault={vault} />
          ))}
        </div>
      </div>
    </div>
  );
}
