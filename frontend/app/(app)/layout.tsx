import { redirect } from "next/navigation";
import { getMe } from "@/lib/auth-server";
import AppShell from "@/components/layout/app-shell";

/**
 * Server component layout for all authenticated pages.
 * Single auth gate — individual pages do not need to call getMe().
 */
export default async function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const user = await getMe();
  if (!user) redirect("/signin");

  return <AppShell user={user}>{children}</AppShell>;
}
