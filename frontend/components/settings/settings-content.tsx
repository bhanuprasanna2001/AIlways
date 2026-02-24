"use client";

import { useState, useEffect } from "react";
import useSWR from "swr";
import { apiFetch, fetcher } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { User } from "@/lib/types";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";

export default function SettingsContent() {
  const { data: user, isLoading, mutate } = useSWR<User>("/api/auth/me", fetcher);
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{
    type: "success" | "error";
    text: string;
  } | null>(null);

  // Sync local state when user data loads
  useEffect(() => {
    if (user) setName(user.name);
  }, [user]);

  const isDirty = user && name.trim() !== "" && name.trim() !== user.name;

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!isDirty) return;

    setSaving(true);
    setMessage(null);

    try {
      await apiFetch("/api/user", {
        method: "PATCH",
        body: { name: name.trim() },
      });
      await mutate();
      setMessage({ type: "success", text: "Profile updated successfully" });
    } catch (err) {
      setMessage({
        type: "error",
        text: err instanceof Error ? err.message : "Failed to update profile",
      });
    } finally {
      setSaving(false);
    }
  }

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner className="h-6 w-6" />
      </div>
    );
  }

  return (
    <div className="max-w-lg">
      <Card>
        <CardHeader>
          <h3 className="text-base font-semibold text-foreground">Profile</h3>
          <p className="mt-0.5 text-sm text-neutral-500 dark:text-neutral-400">
            Manage your account information
          </p>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSave} className="space-y-4">
            <div>
              <label className="mb-1.5 block text-sm font-medium text-foreground">
                Name
              </label>
              <input
                type="text"
                value={name}
                onChange={(e) => {
                  setName(e.target.value);
                  setMessage(null);
                }}
                className="h-11 w-full rounded-lg border border-neutral-200 bg-white px-3.5 text-sm text-foreground outline-none transition-colors placeholder:text-neutral-400 focus:border-foreground dark:border-neutral-800 dark:bg-neutral-950"
              />
            </div>

            <div>
              <label className="mb-1.5 block text-sm font-medium text-foreground">
                Email
              </label>
              <input
                type="email"
                value={user?.email ?? ""}
                disabled
                className="h-11 w-full rounded-lg border border-neutral-200 bg-neutral-50 px-3.5 text-sm text-neutral-500 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-400"
              />
              <p className="mt-1 text-xs text-neutral-400">
                Email cannot be changed
              </p>
            </div>

            {message && (
              <div
                className={cn(
                  "rounded-lg px-4 py-2.5 text-sm",
                  message.type === "success"
                    ? "bg-green-50 text-green-700 dark:bg-green-950/50 dark:text-green-400"
                    : "bg-red-50 text-red-600 dark:bg-red-950/50 dark:text-red-400",
                )}
              >
                {message.text}
              </div>
            )}

            <button
              type="submit"
              disabled={!isDirty || saving}
              className="flex h-10 items-center justify-center rounded-lg bg-foreground px-5 text-sm font-medium text-background transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save Changes"}
            </button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
