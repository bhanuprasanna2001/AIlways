import { NextRequest, NextResponse } from "next/server";

type Params = { params: Promise<{ sessionId: string }> };

/** GET /api/sessions/:id — get session detail with segments and claims. */
export async function GET(req: NextRequest, { params }: Params) {
  const { sessionId } = await params;
  const cookie = req.headers.get("cookie") ?? "";

  try {
    const res = await fetch(
      `${process.env.BACKEND_URL}/sessions/${sessionId}`,
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

/** PATCH /api/sessions/:id — update session title. */
export async function PATCH(req: NextRequest, { params }: Params) {
  const { sessionId } = await params;
  const cookie = req.headers.get("cookie") ?? "";
  const csrfToken = req.cookies.get("csrf_token")?.value ?? "";
  const body = await req.json();

  try {
    const res = await fetch(
      `${process.env.BACKEND_URL}/sessions/${sessionId}`,
      {
        method: "PATCH",
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

/** DELETE /api/sessions/:id — soft-delete a session. */
export async function DELETE(req: NextRequest, { params }: Params) {
  const { sessionId } = await params;
  const cookie = req.headers.get("cookie") ?? "";
  const csrfToken = req.cookies.get("csrf_token")?.value ?? "";

  try {
    const res = await fetch(
      `${process.env.BACKEND_URL}/sessions/${sessionId}`,
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
