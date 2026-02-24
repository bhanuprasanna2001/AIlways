import { NextRequest, NextResponse } from "next/server";

type Params = { params: Promise<{ vaultId: string }> };

/** GET /api/vaults/:id/documents — list documents in a vault. */
export async function GET(req: NextRequest, { params }: Params) {
  const { vaultId } = await params;
  const cookie = req.headers.get("cookie") ?? "";

  try {
    const res = await fetch(
      `${process.env.BACKEND_URL}/vaults/${vaultId}/documents`,
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
