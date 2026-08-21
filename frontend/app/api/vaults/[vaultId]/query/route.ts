import { NextRequest, NextResponse } from "next/server";

type Params = { params: Promise<{ vaultId: string }> };

/** POST /api/vaults/:id/query — query vault documents via RAG pipeline. */
export async function POST(req: NextRequest, { params }: Params) {
  const { vaultId } = await params;
  const cookie = req.headers.get("cookie") ?? "";
  const csrfToken = req.cookies.get("csrf_token")?.value ?? "";
  const body = await req.json();

  try {
    const res = await fetch(
      `${process.env.BACKEND_URL}/vaults/${vaultId}/query`,
      {
        method: "POST",
        headers: {
          cookie,
          "X-CSRF-Token": csrfToken,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
      },
    );

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
