import { NextRequest, NextResponse } from "next/server";

type Params = { params: Promise<{ vaultId: string; docId: string }> };

/** GET /api/vaults/:id/documents/:docId/content — get parsed document content. */
export async function GET(req: NextRequest, { params }: Params) {
  const { vaultId, docId } = await params;
  const cookie = req.headers.get("cookie") ?? "";

  try {
    const res = await fetch(
      `${process.env.BACKEND_URL}/vaults/${vaultId}/documents/${docId}/content`,
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
