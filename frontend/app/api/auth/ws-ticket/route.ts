import { NextRequest, NextResponse } from "next/server";

/** POST /api/auth/ws-ticket — issue a one-time WebSocket ticket. */
export async function POST(req: NextRequest) {
  const cookie = req.headers.get("cookie") ?? "";
  const csrfToken = req.cookies.get("csrf_token")?.value ?? "";

  try {
    const res = await fetch(`${process.env.BACKEND_URL}/auth/ws-ticket`, {
      method: "POST",
      headers: {
        cookie,
        "X-CSRF-Token": csrfToken,
      },
    });

    const text = await res.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text || "Server error" };
    }
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json(
      { detail: "Service unavailable" },
      { status: 503 },
    );
  }
}
