import { NextRequest, NextResponse } from "next/server";

export async function POST(req: NextRequest) {
  const cookie = req.headers.get("cookie") ?? "";
  const csrfToken = req.cookies.get("csrf_token")?.value ?? "";

  try {
    const backendRes = await fetch(`${process.env.BACKEND_URL}/auth/logout`, {
      method: "POST",
      headers: {
        cookie,
        "X-CSRF-Token": csrfToken,
      },
    });

    const data = await backendRes.json().catch(() => ({}));
    const res = NextResponse.json(data, { status: backendRes.status });

    // Always clear cookies client-side
    res.cookies.delete("session_id");
    res.cookies.delete("csrf_token");

    return res;
  } catch {
    // Backend unreachable — still clear cookies so the user isn't stuck
    const res = NextResponse.json(
      { message: "Logged out" },
      { status: 200 },
    );
    res.cookies.delete("session_id");
    res.cookies.delete("csrf_token");

    return res;
  }
}
