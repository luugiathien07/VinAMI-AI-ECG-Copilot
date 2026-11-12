import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { serverConfig } from "@web/shared/config/server";

const allowedPaths = [
  /^health$/,
  /^ready$/,
  /^api\/v1\/identity\/session$/,
  /^api\/v1\/clinical-cases$/,
  /^api\/v1\/command-center\/cases$/,
  /^api\/v1\/clinical-cases\/[0-9a-f-]+(?:\/timeline|\/audit|\/triage|\/triage\/history|\/vitals|\/vitals\/latest|\/triage-vitals-summary|\/complete-workspace|\/outcomes|\/model-provenance)?$/,
  /^api\/v1\/clinical-cases\/[0-9a-f-]+\/history\/(?:timeline|audit)$/,
  /^api\/v1\/research-exports\/[0-9a-f-]+$/,
  /^api\/v1\/realtime\/events\/snapshot$/,
  /^api\/v1\/integration\/(?:messages|connectors\/status)$/,
  /^api\/v1\/clinical-cases\/[0-9a-f-]+\/clinical-reasoning(?:\/risk-scores)?$/,
  /^api\/v1\/clinical-cases\/[0-9a-f-]+\/workflow-operations$/,
  /^api\/v1\/clinical-cases\/[0-9a-f-]+\/ecg-workspace$/,
  /^api\/v1\/clinical-cases\/[0-9a-f-]+\/(?:ecg-imports|ecg-comparisons|ecg-evolution|repeat-ecg-reminders)$/,
  /^api\/v1\/ecg-source-connectors(?:\/[a-z0-9-]+\/health)?$/,
  /^api\/v1\/ecg-studies\/[0-9a-f-]+\/(?:waveform|quality|analyses|physician-interpretations|preview)$/,
  /^api\/v1\/ecg-analyses\/[0-9a-f-]+\/explanation$/,
  /^api\/v1\/ecg-ingestion-jobs\/[0-9a-f-]+$/,
  /^api\/v1\/ecg-comparisons\/[0-9a-f-]+(?:\/physician-reviews)?$/,
  /^api\/v1\/repeat-ecg-reminders\/[0-9a-f-]+$/,
  /^api\/v1\/clinical-cases\/[0-9a-f-]+\/(?:troponin-results|troponin-series|troponin-deltas|troponin-workspace|troponin-imports|troponin-reviews|repeat-troponin-requests)$/,
  /^api\/v1\/troponin-results\/[0-9a-f-]+$/,
  /^api\/v1\/laboratory-import-jobs\/[0-9a-f-]+$/,
  /^api\/v1\/troponin-assays(?:\/[0-9a-f-]+)?$/,
  /^api\/v1\/laboratory-connectors(?:\/[a-z0-9-]+\/health)?$/,
  /^api\/v1\/encounters\/[0-9a-f-]+$/,
  /^api\/v1\/patients\/[0-9a-f-]+$/,
];

const allowedCommandPaths = [
  /^api\/v1\/clinical-cases\/[0-9a-f-]+\/triage-assessments$/,
  /^api\/v1\/clinical-cases\/[0-9a-f-]+\/triage-assessments\/[0-9a-f-]+\/corrections$/,
  /^api\/v1\/clinical-cases\/[0-9a-f-]+\/vital-observation-sets$/,
  /^api\/v1\/clinical-cases\/[0-9a-f-]+\/vital-observation-sets\/[0-9a-f-]+\/corrections$/,
  /^api\/v1\/clinical-cases\/[0-9a-f-]+\/ecg-studies$/,
  /^api\/v1\/clinical-cases\/[0-9a-f-]+\/(?:ecg-imports|ecg-comparisons|repeat-ecg-reminders)$/,
  /^api\/v1\/ecg-studies\/[0-9a-f-]+\/(?:analyses|physician-interpretations)$/,
  /^api\/v1\/ecg-ingestion-jobs\/[0-9a-f-]+\/retry$/,
  /^api\/v1\/ecg-comparisons\/[0-9a-f-]+\/physician-reviews$/,
  /^api\/v1\/ecg-comparisons\/[0-9a-f-]+\/physician-reviews\/[0-9a-f-]+\/corrections$/,
  /^api\/v1\/repeat-ecg-reminders\/[0-9a-f-]+\/(?:acknowledge|complete|cancel)$/,
  /^api\/v1\/clinical-cases\/[0-9a-f-]+\/(?:troponin-results|troponin-imports|troponin-reviews)$/,
  /^api\/v1\/clinical-cases\/[0-9a-f-]+\/troponin-reviews\/[0-9a-f-]+\/corrections$/,
  /^api\/v1\/troponin-results\/[0-9a-f-]+\/amendments$/,
  /^api\/v1\/laboratory-import-jobs\/[0-9a-f-]+\/retry$/,
  /^api\/v1\/repeat-troponin-requests\/[0-9a-f-]+\/(?:acknowledge|complete|cancel)$/,
  /^api\/v1\/clinical-cases\/[0-9a-f-]+\/clinical-reasoning\/(?:snapshots|assessments|decisions)$/,
  /^api\/v1\/clinical-cases\/[0-9a-f-]+\/clinical-reasoning\/risk-scores\/(?:heart|grace)$/,
  /^api\/v1\/clinical-cases\/[0-9a-f-]+\/clinical-reasoning\/(?:assessments|decisions)\/[0-9a-f-]+\/(?:corrections|revocations)$/,
  /^api\/v1\/clinical-cases\/[0-9a-f-]+\/workflow-operations\/(?:tasks\/from-decisions|overdue-evaluations)$/,
  /^api\/v1\/clinical-cases\/[0-9a-f-]+\/workflow-operations\/tasks\/[0-9a-f-]+\/(?:assign|acknowledge|start|complete|cancel)$/,
  /^api\/v1\/clinical-cases\/[0-9a-f-]+\/workflow-operations\/alerts\/[0-9a-f-]+\/(?:acknowledge|resolve)$/,
  /^api\/v1\/clinical-cases\/[0-9a-f-]+\/outcomes$/,
  /^api\/v1\/clinical-cases\/[0-9a-f-]+\/outcomes\/[0-9a-f-]+\/corrections$/,
  /^api\/v1\/research-exports$/,
  /^api\/v1\/integration\/simulated\/(?:laboratory-results|ecg-studies)$/,
  /^api\/v1\/integration\/messages\/[0-9a-f-]+\/retry$/,
];

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  return forward(request, context, "GET", allowedPaths);
}

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  return forward(request, context, "POST", allowedCommandPaths);
}

async function forward(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
  method: "GET" | "POST",
  allowList: RegExp[],
) {
  const { path } = await context.params;
  const joined = path.join("/");
  if (!allowList.some((pattern) => pattern.test(joined))) {
    return problem(
      404,
      "Unsupported frontend resource",
      "This resource is not exposed through the approved frontend boundary.",
    );
  }
  const correlationId =
    validUuid(request.headers.get("x-correlation-id")) ?? crypto.randomUUID();
  const target = new URL(
    joined,
    `${serverConfig.apiBaseUrl.replace(/\/$/, "")}/`,
  );
  target.search = request.nextUrl.search;
  const headers = new Headers({
    Accept: "application/json",
    "X-Correlation-ID": correlationId,
    "X-Request-Source": "vinami-next-bff",
  });
  if (method === "POST") {
    const contentType = request.headers.get("content-type");
    if (contentType) headers.set("Content-Type", contentType);
    const idempotencyKey = request.headers.get("idempotency-key");
    if (idempotencyKey) headers.set("Idempotency-Key", idempotencyKey);
  }
  if (serverConfig.identityMode === "development") {
    headers.set("X-Development-Identity", serverConfig.developmentIdentity);
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 9_000);
  try {
    const response = await fetch(target, {
      method,
      headers,
      body: method === "POST" ? await request.arrayBuffer() : undefined,
      cache: "no-store",
      signal: controller.signal,
    });
    return new NextResponse(response.body, {
      status: response.status,
      headers: {
        "content-type":
          response.headers.get("content-type") ?? "application/json",
        "x-correlation-id":
          response.headers.get("x-correlation-id") ?? correlationId,
        "cache-control": "no-store",
      },
    });
  } catch (error) {
    const timeoutFailure =
      error instanceof DOMException && error.name === "AbortError";
    return problem(
      timeoutFailure ? 504 : 503,
      timeoutFailure ? "Upstream timeout" : "Upstream unavailable",
      timeoutFailure
        ? "The clinical service did not respond in time."
        : "The clinical service could not be reached.",
      correlationId,
    );
  } finally {
    clearTimeout(timeout);
  }
}

function validUuid(value: string | null): string | null {
  return value &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
      value,
    )
    ? value
    : null;
}

function problem(
  status: number,
  title: string,
  detail: string,
  correlationId = crypto.randomUUID(),
) {
  return NextResponse.json(
    {
      type: "https://vinami.example/problems/frontend-boundary",
      title,
      status,
      detail,
      instance: "frontend-boundary",
      correlation_id: correlationId,
    },
    {
      status,
      headers: {
        "content-type": "application/problem+json",
        "x-correlation-id": correlationId,
      },
    },
  );
}
