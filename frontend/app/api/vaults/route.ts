import { NextRequest, NextResponse } from "next/server";

/** GET /api/vaults — list vaults for the current user. */
export async function GET(req: NextRequest) {
  const cookie = req.headers.get("cookie") ?? "";

  try {
    const res = await fetch(`${process.env.BACKEND_URL}/vaults`, {
      headers: { cookie },
      cache: "no-store",
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json(
      { detail: "Service unavailable" },
      { status: 503 },
    );
  }
}

/** POST /api/vaults — create a new vault. */
export async function POST(req: NextRequest) {
  const cookie = req.headers.get("cookie") ?? "";
  const csrfToken = req.cookies.get("csrf_token")?.value ?? "";
  const body = await req.json();

  try {
    const res = await fetch(`${process.env.BACKEND_URL}/vaults`, {
      method: "POST",
      headers: {
        cookie,
        "X-CSRF-Token": csrfToken,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
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
