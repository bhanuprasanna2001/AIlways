import { NextRequest, NextResponse } from "next/server";

type Params = { params: Promise<{ vaultId: string }> };

/** GET /api/vaults/:id/documents — list documents in a vault.
 *  Forwards `skip` and `limit` query params to the backend and
 *  passes through the `X-Total-Count` response header so the
 *  frontend can do proper server-side pagination. */
export async function GET(req: NextRequest, { params }: Params) {
  const { vaultId } = await params;
  const cookie = req.headers.get("cookie") ?? "";

  // Forward pagination + search params from the client
  const skip = req.nextUrl.searchParams.get("skip") ?? "0";
  const limit = req.nextUrl.searchParams.get("limit");
  const search = req.nextUrl.searchParams.get("search");
  const qs = new URLSearchParams({ skip });
  if (limit) qs.set("limit", limit);
  if (search) qs.set("search", search);

  try {
    const res = await fetch(
      `${process.env.BACKEND_URL}/vaults/${vaultId}/documents?${qs}`,
      {
        headers: { cookie },
        cache: "no-store",
      },
    );
    const data = await res.json();
    const resp = NextResponse.json(data, { status: res.status });

    // Forward total count so the client knows how many pages exist
    const total = res.headers.get("X-Total-Count");
    if (total) {
      resp.headers.set("X-Total-Count", total);
    }
    return resp;
  } catch {
    return NextResponse.json(
      { detail: "Service unavailable" },
      { status: 503 },
    );
  }
}
