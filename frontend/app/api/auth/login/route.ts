import { NextRequest, NextResponse } from "next/server";

export async function POST(req: NextRequest) {
  const body = await req.json();

  const backendRes = await fetch(`${process.env.BACKEND_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  const text = await backendRes.text();
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(text);
  } catch {
    data = { detail: text || "Server error" };
  }
  const res = NextResponse.json(data, { status: backendRes.status });

  // Forward Set-Cookie headers (session_id + csrf_token) from backend
  for (const cookie of backendRes.headers.getSetCookie()) {
    res.headers.append("Set-Cookie", cookie);
  }

  return res;
}
