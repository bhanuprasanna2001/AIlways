import Link from "next/link";
import { getMe } from "@/lib/auth-server";

export default async function Home() {
  const user = await getMe();

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#fafafa] font-sans dark:bg-[#252525]">
      <main className="flex min-h-screen w-full max-w-3xl flex-col items-center justify-between py-32 px-16 bg-[#fafafa] dark:bg-[#252525] sm:items-start">
        {/* Title */}
        <h1 className="text-5xl font-extrabold tracking-tight text-[#252525] dark:text-[#fafafa] sm:text-[5rem]">
          AIlways
        </h1>
        <div className="flex flex-col items-center gap-6 text-center sm:items-start sm:text-left">
          <h1 className="max-w-xs text-3xl font-semibold leading-10 tracking-tight text-black dark:text-zinc-50">
            Meeting Truth &amp; Context Copilot
          </h1>
        </div>
        <div className="flex flex-col gap-4 text-base font-medium sm:flex-row">
          {user ? (
            <Link
              className="flex h-12 w-full items-center justify-center gap-2 rounded-full bg-foreground px-5 text-background transition-colors hover:bg-[#383838] dark:hover:bg-[#ccc] md:w-[158px]"
              href="/dashboard"
            >
              Dashboard
            </Link>
          ) : (
            <>
              <Link
                className="flex h-12 w-full items-center justify-center gap-2 rounded-full bg-foreground px-5 text-background transition-colors hover:bg-[#383838] dark:hover:bg-[#ccc] md:w-[158px]"
                href="/signup"
              >
                Sign Up
              </Link>
              <Link
                className="flex h-12 w-full items-center justify-center rounded-full border border-solid border-black/[.08] px-5 transition-colors hover:border-transparent hover:bg-black/[.04] dark:border-white/[.145] dark:hover:bg-[#1a1a1a] md:w-[158px]"
                href="/signin"
              >
                Sign In
              </Link>
            </>
          )}
        </div>
      </main>
    </div>
  );
}
