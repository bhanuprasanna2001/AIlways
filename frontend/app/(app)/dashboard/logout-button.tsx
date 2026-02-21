"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export default function LogoutButton() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);

  async function handleLogout() {
    setLoading(true);
    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } catch {
      // Proceed to signin even if logout request fails
    }
    router.push("/signin");
    router.refresh();
  }

  return (
    <button
      onClick={handleLogout}
      disabled={loading}
      className="cursor-pointer rounded-lg border border-neutral-200 px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-neutral-800 dark:hover:bg-neutral-900"
    >
      {loading ? "Signing out…" : "Sign Out"}
    </button>
  );
}
