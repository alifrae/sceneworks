import { NextResponse } from "next/server";

import { getSupervisorStatus } from "@/lib/supervisor";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    return NextResponse.json(await getSupervisorStatus(), {
      headers: { "Cache-Control": "no-store" },
    });
  } catch {
    return NextResponse.json(
      { error: "supervisor_unavailable" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
