import { NextRequest, NextResponse } from "next/server";

/**
 * Lightweight middleware — only blocks unauthenticated access to protected routes.
 *
 * Does NOT redirect auth pages → dashboard (that's handled by server component
 * gates via getMe()). This avoids the classic "stale cookie → redirect loop"
 * problem where the cookie exists but the backend session has expired.
 */
export function middleware(req: NextRequest) {
  const hasSession = req.cookies.has("session_id");

  if (!hasSession) {
    return NextResponse.redirect(new URL("/signin", req.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/dashboard/:path*"],
};
