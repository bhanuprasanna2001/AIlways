"use client";

import { SidebarProvider } from "./sidebar-context";
import AppSidebar from "./app-sidebar";
import AppHeader from "./app-header";
import type { User } from "@/lib/types";

export default function AppShell({
  user,
  children,
}: {
  user: User;
  children: React.ReactNode;
}) {
  return (
    <SidebarProvider>
      <div className="min-h-screen bg-[#FAFAFA] dark:bg-[#252525]">
        <AppSidebar />
        <div className="lg:ml-[240px]">
          <AppHeader user={user} />
          <main className="mx-auto max-w-6xl px-6 py-8">{children}</main>
        </div>
      </div>
    </SidebarProvider>
  );
}
