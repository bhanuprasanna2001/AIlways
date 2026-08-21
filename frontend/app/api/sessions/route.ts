import { NextRequest, NextResponse } from "next/server";

/** GET /api/sessions — list transcription sessions for the current user. */
export async function GET(req: NextRequest) {
  const cookie = req.headers.get("cookie") ?? "";

  try {
    const res = await fetch(`${process.env.BACKEND_URL}/sessions`, {
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
