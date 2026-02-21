"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

export default function SignUpForm() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const res = await fetch("/api/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, email, password }),
      });

      const data = await res.json();

      if (!res.ok) {
        if (Array.isArray(data.detail)) {
          setError(data.detail.map((d: { msg: string }) => d.msg).join(". "));
        } else {
          setError(data.detail || "Something went wrong");
        }
        return;
      }

      router.push("/signin");
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      {error && (
        <div className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-600 dark:bg-red-950/50 dark:text-red-400">
          {error}
        </div>
      )}

      <div>
        <label
          htmlFor="name"
          className="mb-1.5 block text-sm font-medium text-foreground"
        >
          Name
        </label>
        <input
          id="name"
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          autoComplete="name"
          className="h-11 w-full rounded-lg border border-neutral-200 bg-white px-3.5 text-sm text-foreground outline-none transition-colors placeholder:text-neutral-400 focus:border-foreground dark:border-neutral-800 dark:bg-neutral-950 dark:placeholder:text-neutral-600"
          placeholder="Your name"
        />
      </div>

      <div>
        <label
          htmlFor="email"
          className="mb-1.5 block text-sm font-medium text-foreground"
        >
          Email
        </label>
        <input
          id="email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          autoComplete="email"
          className="h-11 w-full rounded-lg border border-neutral-200 bg-white px-3.5 text-sm text-foreground outline-none transition-colors placeholder:text-neutral-400 focus:border-foreground dark:border-neutral-800 dark:bg-neutral-950 dark:placeholder:text-neutral-600"
          placeholder="you@example.com"
        />
      </div>

      <div>
        <label
          htmlFor="password"
          className="mb-1.5 block text-sm font-medium text-foreground"
        >
          Password
        </label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          minLength={8}
          autoComplete="new-password"
          className="h-11 w-full rounded-lg border border-neutral-200 bg-white px-3.5 text-sm text-foreground outline-none transition-colors placeholder:text-neutral-400 focus:border-foreground dark:border-neutral-800 dark:bg-neutral-950 dark:placeholder:text-neutral-600"
          placeholder="••••••••"
        />
        <p className="mt-1.5 text-xs text-neutral-400 dark:text-neutral-600">
          Min 8 characters, 1 uppercase letter, 1 digit
        </p>
      </div>

      <button
        type="submit"
        disabled={loading}
        className="mt-2 flex h-11 cursor-pointer items-center justify-center rounded-lg bg-foreground text-sm font-medium text-background transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {loading ? "Creating account…" : "Sign Up"}
      </button>

      <p className="text-center text-sm text-neutral-500 dark:text-neutral-400">
        Already have an account?{" "}
        <Link
          href="/signin"
          className="font-medium text-foreground underline-offset-4 hover:underline"
        >
          Sign In
        </Link>
      </p>
    </form>
  );
}
