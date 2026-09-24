import { NextRequest, NextResponse } from "next/server";

const NGROK_FREE_HOSTS = ["ngrok-free.app", "ngrok-free.dev"];

export function proxy(request: NextRequest) {
  const configured = process.env.API_BASE_URL?.trim();
  if (!configured || !isNgrokFreeHost(new URL(configured).hostname)) {
    return NextResponse.next();
  }

  const upstreamHeaders = new Headers(request.headers);
  upstreamHeaders.set("ngrok-skip-browser-warning", "true");
  return NextResponse.next({ request: { headers: upstreamHeaders } });
}

function isNgrokFreeHost(hostname: string) {
  const normalized = hostname.toLowerCase().replace(/\.$/, "");
  return NGROK_FREE_HOSTS.some(
    (allowed) => normalized === allowed || normalized.endsWith(`.${allowed}`),
  );
}

export const config = {
  matcher: "/api/:path*",
};
