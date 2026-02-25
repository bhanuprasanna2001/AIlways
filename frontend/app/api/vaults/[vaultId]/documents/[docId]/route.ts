import { NextRequest, NextResponse } from "next/server";

type Params = { params: Promise<{ vaultId: string; docId: string }> };

/** GET /api/vaults/:id/documents/:docId — get single document details. */
export async function GET(req: NextRequest, { params }: Params) {
  const { vaultId, docId } = await params;
  const cookie = req.headers.get("cookie") ?? "";

  try {
    const res = await fetch(
      `${process.env.BACKEND_URL}/vaults/${vaultId}/documents/${docId}`,
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

/** DELETE /api/vaults/:id/documents/:docId — delete a document. */
export async function DELETE(req: NextRequest, { params }: Params) {
  const { vaultId, docId } = await params;
  const cookie = req.headers.get("cookie") ?? "";
  const csrfToken = req.cookies.get("csrf_token")?.value ?? "";

  try {
    const res = await fetch(
      `${process.env.BACKEND_URL}/vaults/${vaultId}/documents/${docId}`,
      {
        method: "DELETE",
        headers: {
          cookie,
          "X-CSRF-Token": csrfToken,
        },
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
