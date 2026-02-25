import { NextRequest, NextResponse } from "next/server";

type Params = { params: Promise<{ vaultId: string }> };

/**
 * POST /api/vaults/:id/documents/upload — upload a file for ingestion.
 *
 * Forwards the raw FormData to the backend. Does NOT set Content-Type
 * so fetch automatically includes the multipart boundary.
 */
export async function POST(req: NextRequest, { params }: Params) {
  const { vaultId } = await params;
  const cookie = req.headers.get("cookie") ?? "";
  const csrfToken = req.cookies.get("csrf_token")?.value ?? "";

  try {
    const formData = await req.formData();

    const res = await fetch(
      `${process.env.BACKEND_URL}/vaults/${vaultId}/documents/upload`,
      {
        method: "POST",
        headers: {
          cookie,
          "X-CSRF-Token": csrfToken,
        },
        body: formData,
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
