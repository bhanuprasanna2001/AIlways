"use client";

import { useState, useRef, useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Menu, LogOut, ChevronDown, Settings } from "lucide-react";
import { useSidebar } from "./sidebar-context";
import { CONVERSATIONS_STORAGE_KEY } from "@/lib/constants";
import { apiFetch } from "@/lib/api";
import type { User } from "@/lib/types";

const PAGE_TITLES: Record<string, string> = {
  "/dashboard": "Dashboard",
  "/copilot": "Copilot",
  "/transcription": "Transcription",
  "/history": "History",
  "/sessions": "Sessions",
  "/vaults": "Vaults",
  "/settings": "Settings",
};

function getPageTitle(pathname: string): string {
  if (PAGE_TITLES[pathname]) return PAGE_TITLES[pathname];
  if (pathname.startsWith("/vaults/")) return "Vault";
  return "";
}

export default function AppHeader({ user }: { user: User }) {
  const pathname = usePathname();
  const router = useRouter();
  const { toggleMobileSidebar } = useSidebar();
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    if (!dropdownOpen) return;
    function handleClick(e: MouseEvent) {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node)
      ) {
        setDropdownOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [dropdownOpen]);

  function handleLogout() {
    setDropdownOpen(false);
    // Clear user-scoped client data to prevent data leak between users
    try {
      localStorage.removeItem(CONVERSATIONS_STORAGE_KEY);
    } catch { /* ignore */ }
    apiFetch("/api/auth/logout", { method: "POST" }).catch(() => {/* ignore */}).finally(() => {
      router.push("/signin");
      router.refresh();
    });
  }

  const initials = (user.name || "?")
    .split(" ")
    .map((n) => n[0])
    .filter(Boolean)
    .join("")
    .toUpperCase()
    .slice(0, 2) || "?";

  return (
    <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-neutral-200 bg-white/80 px-6 backdrop-blur dark:border-neutral-800 dark:bg-[#252525]/80">
      <div className="flex items-center gap-4">
        <button
          onClick={toggleMobileSidebar}
          className="rounded-md p-1 text-neutral-500 transition-colors hover:text-foreground lg:hidden"
        >
          <Menu className="h-5 w-5" />
        </button>
        <h1 className="text-lg font-semibold text-foreground">
          {getPageTitle(pathname)}
        </h1>
      </div>

      {/* User dropdown */}
      <div ref={dropdownRef} className="relative">
        <button
          onClick={() => setDropdownOpen(!dropdownOpen)}
          className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm transition-colors hover:bg-neutral-100 dark:hover:bg-neutral-800"
        >
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-neutral-200 text-xs font-medium text-neutral-600 dark:bg-neutral-700 dark:text-neutral-300">
            {initials}
          </div>
          <span className="hidden text-foreground sm:inline">{user.name}</span>
          <ChevronDown className="h-4 w-4 text-neutral-400" />
        </button>

        {dropdownOpen && (
          <div className="absolute right-0 mt-2 w-48 rounded-lg border border-neutral-200 bg-white py-1 shadow-lg dark:border-neutral-700 dark:bg-neutral-900">
            <button
              onClick={() => {
                setDropdownOpen(false);
                router.push("/settings");
              }}
              className="flex w-full items-center gap-2 px-4 py-2 text-sm text-neutral-600 transition-colors hover:bg-neutral-50 dark:text-neutral-300 dark:hover:bg-neutral-800"
            >
              <Settings className="h-4 w-4" />
              Settings
            </button>
            <button
              onClick={handleLogout}
              className="flex w-full items-center gap-2 px-4 py-2 text-sm text-red-600 transition-colors hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/50"
            >
              <LogOut className="h-4 w-4" />
              Sign Out
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
