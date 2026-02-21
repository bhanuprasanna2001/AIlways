import { redirect } from "next/navigation";
import { getMe } from "@/lib/auth-server";
import LogoutButton from "./logout-button";

export default async function DashboardPage() {
  const user = await getMe();
  if (!user) redirect("/signin");

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-neutral-200 dark:border-neutral-800">
        <div className="mx-auto flex h-16 max-w-5xl items-center justify-between px-6">
          <h1 className="text-lg font-bold text-foreground">AIlways</h1>
          <div className="flex items-center gap-4">
            <span className="text-sm text-neutral-500 dark:text-neutral-400">
              {user.name}
            </span>
            <LogoutButton />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-6 py-12">
        <h2 className="text-2xl font-bold text-foreground">Dashboard</h2>
        <p className="mt-2 text-neutral-500 dark:text-neutral-400">
          Welcome back, {user.name}.
        </p>
      </main>
    </div>
  );
}
