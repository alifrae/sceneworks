import { NextResponse } from "next/server";

import { restartSupervisor, validateSupervisorComponent } from "@/lib/supervisor";

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }
  if (!body || typeof body !== "object" || Object.keys(body).some((key) => key !== "component")) {
    return NextResponse.json({ error: "invalid_request" }, { status: 400 });
  }
  const component = (body as { component?: unknown }).component;
  try {
    validateSupervisorComponent(component);
  } catch {
    return NextResponse.json({ error: "invalid_component" }, { status: 400 });
  }
  try {
    return NextResponse.json(await restartSupervisor(String(component)), { status: 202 });
  } catch {
    return NextResponse.json({ error: "supervisor_unavailable" }, { status: 503 });
  }
}
