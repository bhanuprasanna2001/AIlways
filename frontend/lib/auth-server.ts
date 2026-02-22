import { headers } from "next/headers";

export type User = {
  id: string;
  name: string;
  email: string;
};

/**
 * Server-only helper: calls backend /auth/me with forwarded cookies.
 * - `headers()` opts the route into dynamic rendering (no stale cache).
 * - `cache: "no-store"` ensures every call hits the backend.
 * - Returns `null` on any failure (401, network error, etc.).
 */
export async function getMe(): Promise<User | null> {
  try {
    const cookie = (await headers()).get("cookie") ?? "";

    const res = await fetch(`${process.env.BACKEND_URL}/auth/me`, {
      headers: { cookie },
      cache: "no-store",
    });

    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}
