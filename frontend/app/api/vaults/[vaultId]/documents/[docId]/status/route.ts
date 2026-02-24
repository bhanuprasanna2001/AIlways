import { NextRequest, NextResponse } from "next/server";

type Params = { params: Promise<{ vaultId: string; docId: string }> };

/** GET /api/vaults/:id/documents/:docId/status — poll ingestion status. */
export async function GET(req: NextRequest, { params }: Params) {
  const { vaultId, docId } = await params;
  const cookie = req.headers.get("cookie") ?? "";

  try {
    const res = await fetch(
      `${process.env.BACKEND_URL}/vaults/${vaultId}/documents/${docId}/status`,
      {
        headers: { cookie },
        cache: "no-store",
      },
    );
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json(
      { detail: "Service unavailable" },
      { status: 503 },
    );
  }
}
