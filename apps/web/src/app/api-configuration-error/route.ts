import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export function GET() { return unavailable(); }
export function POST() { return unavailable(); }
export function PATCH() { return unavailable(); }
export function DELETE() { return unavailable(); }

function unavailable() {
  return NextResponse.json(
    {
      detail: {
        code: "BACKEND_NOT_CONFIGURED",
        message: "Backend API is not configured. Set API_BASE_URL to the public HTTPS FastAPI origin and redeploy.",
      },
    },
    { status: 503, headers: { "Cache-Control": "no-store" } },
  );
}
