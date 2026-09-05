import { NextResponse } from "next/server";

import { getSupervisorOperation } from "@/lib/supervisor";

export const dynamic = "force-dynamic";

export async function GET(
  _request: Request,
  context: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await context.params;
    return NextResponse.json(await getSupervisorOperation(id), {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "supervisor unavailable";
    const status = message === "invalid operation id" ? 400 : 503;
    return NextResponse.json(
      { error: status === 400 ? "invalid_operation_id" : "supervisor_unavailable" },
      { status, headers: { "Cache-Control": "no-store" } },
    );
  }
}
