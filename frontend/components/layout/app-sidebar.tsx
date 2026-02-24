"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  MessageSquare,
  FolderLock,
  Clock,
  Settings,
  X,
} from "lucide-react";
import { useSidebar } from "./sidebar-context";
import { cn } from "@/lib/utils";

const navItems = [
  { icon: LayoutDashboard, label: "Dashboard", path: "/dashboard" },
  { icon: MessageSquare, label: "Copilot", path: "/copilot" },
  { icon: FolderLock, label: "Vaults", path: "/vaults" },
  { icon: Clock, label: "History", path: "/history" },
  { icon: Settings, label: "Settings", path: "/settings" },
];

export default function AppSidebar() {
  const pathname = usePathname();
  const { isMobileOpen, toggleMobileSidebar } = useSidebar();

  return (
    <>
      {/* Backdrop — mobile only */}
      {isMobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/40 lg:hidden"
          onClick={toggleMobileSidebar}
        />
      )}

      {/* Sidebar */}
      <aside
        className={cn(
          "fixed left-0 top-0 z-50 flex h-full w-[240px] flex-col border-r border-neutral-200 bg-white dark:border-neutral-800 dark:bg-[#1a1a1a]",
          "transition-transform duration-300 ease-in-out lg:translate-x-0",
          isMobileOpen ? "translate-x-0" : "-translate-x-full",
        )}
      >
        {/* Logo */}
        <div className="flex h-16 items-center justify-between px-6">
          <Link
            href="/dashboard"
            className="text-lg font-bold tracking-tight text-foreground"
          >
            AIlways
          </Link>
          <button
            onClick={toggleMobileSidebar}
            className="rounded-md p-1 text-neutral-400 transition-colors hover:text-neutral-600 lg:hidden dark:hover:text-neutral-300"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Navigation */}
        <nav className="flex-1 px-3 py-4">
          <ul className="space-y-1">
            {navItems.map((item) => {
              const isActive =
                pathname === item.path ||
                pathname.startsWith(item.path + "/");

              return (
                <li key={item.path}>
                  <Link
                    href={item.path}
                    onClick={() => isMobileOpen && toggleMobileSidebar()}
                    className={cn(
                      "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors",
                      isActive
                        ? "bg-neutral-100 text-foreground dark:bg-neutral-800"
                        : "text-neutral-500 hover:bg-neutral-50 hover:text-foreground dark:text-neutral-400 dark:hover:bg-neutral-800/50",
                    )}
                  >
                    <item.icon className="h-[18px] w-[18px] shrink-0" />
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      </aside>
    </>
  );
}
