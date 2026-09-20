/**
 * The API client.
 *
 * Every shape here comes from `api-types.ts`, which is generated from the delivered
 * OpenAPI contract — the frontend never declares a competing schema of its own. Requests
 * go to this same origin and Next.js rewrites them to FastAPI, so the session cookie
 * travels normally and no CORS policy is needed.
 */

import type { components, paths } from "./api-types";

export type Schemas = components["schemas"];

export type ErrorResponse = Schemas["ErrorResponse"];
export type CurrentUser = Schemas["CurrentUser"];
export type DemoAccount = Schemas["DemoAccount"];
export type ServiceTemplateOut = Schemas["ServiceTemplateOut"];
export type CoverageResult = Schemas["CoverageResult"];
export type PolicySummary = Schemas["PolicySummary"];
export type RequestOut = Schemas["RequestOut"];
export type PaymentOut = Schemas["PaymentOut"];
export type OfferOut = Schemas["OfferOut"];
export type OfferComparisonOut = Schemas["OfferComparisonOut"];
export type SelectionOut = Schemas["SelectionOut"];
export type AgreementOut = Schemas["AgreementOut"];
export type ExpenseVersionOut = Schemas["ExpenseVersionOut"];
export type CompletionOut = Schemas["CompletionOut"];
export type DisputeOut = Schemas["DisputeOut"];
export type RefundOut = Schemas["RefundOut"];
export type AiRunOut = Schemas["AiRunOut"];
export type AiUsageOut = Schemas["AiUsageOut"];
export type PartOptionOut = Schemas["PartOptionOut"];
export type PriceSnapshotOut = Schemas["PriceSnapshotOut"];
export type PriceCheckOut = Schemas["PriceCheckOut"];
export type LineItem = Schemas["LineItem"];
export type SupportQueueOut = Schemas["SupportQueueOut"];
export type MetricsOut = Schemas["MetricsOut"];
export type DemoStateOut = Schemas["DemoStateOut"];
export type EvaluationOut = Schemas["EvaluationOut"];

export type Paths = paths;

/** A failed call, carrying the server's own error contract. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fieldErrors: Record<string, string> | null;
  readonly currentRevision: number | null;

  constructor(status: number, body: ErrorResponse) {
    super(body.message);
    this.name = "ApiError";
    this.status = status;
    this.code = body.code;
    this.fieldErrors = body.fieldErrors ?? null;
    this.currentRevision = body.currentRevision ?? null;
  }
}

const FALLBACK_MESSAGE = "ارتباط با سرور برقرار نشد. لطفاً دوباره تلاش کنید.";

type RequestOptions = {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  idempotencyKey?: string;
  signal?: AbortSignal;
};

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, idempotencyKey, signal } = options;
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;

  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch {
    throw new ApiError(0, { code: "SERVICE_UNAVAILABLE", message: FALLBACK_MESSAGE });
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const parsed: unknown = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const body = (parsed ?? {}) as Partial<ErrorResponse>;
    throw new ApiError(response.status, {
      code: body.code ?? "SERVICE_UNAVAILABLE",
      message: body.message ?? FALLBACK_MESSAGE,
      fieldErrors: body.fieldErrors,
      currentRevision: body.currentRevision,
    });
  }
  return parsed as T;
}

/** Whole Toman, grouped with Persian digits. */
export function toman(amount: number | null | undefined): string {
  if (amount === null || amount === undefined) return "نامعلوم";
  return `${amount.toLocaleString("fa-IR")} تومان`;
}

/** Stored times are UTC; people read them in Tehran time. */
export function tehranTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("fa-IR", {
    timeZone: "Asia/Tehran",
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export function relativeDeadline(value: string | null | undefined, now: Date): string {
  if (!value) return "—";
  const remaining = new Date(value).getTime() - now.getTime();
  if (remaining <= 0) return "به پایان رسیده";
  const hours = Math.floor(remaining / 3_600_000);
  const minutes = Math.floor((remaining % 3_600_000) / 60_000);
  if (hours >= 24) return `${Math.floor(hours / 24).toLocaleString("fa-IR")} روز باقی مانده`;
  if (hours > 0) return `${hours.toLocaleString("fa-IR")} ساعت باقی مانده`;
  return `${minutes.toLocaleString("fa-IR")} دقیقه باقی مانده`;
}

export const REQUEST_STATUS_LABELS: Record<string, string> = {
  draft: "پیش‌نویس",
  open: "در انتظار پیشنهاد",
  selecting: "در انتظار پذیرش متخصص",
  assigned: "همکاری پذیرفته‌شده",
  in_progress: "در حال انجام",
  completion_review: "در انتظار تأیید پایان کار",
  completed: "تکمیل‌شده",
  closed_unselected: "بسته‌شده بدون انتخاب",
  cancelled: "لغو شده",
  dispute_open: "در حال رسیدگی به اختلاف",
  closed_settled: "بسته‌شده با توافق",
  closed_adjudicated: "بسته‌شده با حکم داوری",
};

export const OFFER_TYPE_LABELS: Record<string, string> = {
  fixed: "قطعی",
  conditional: "مشروط",
  diagnostic: "عیب‌یابی",
};
