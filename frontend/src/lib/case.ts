/** Shared loaders for a single case, used by the per-case pages. */

import {
  api,
  type AgreementOut,
  type CompletionOut,
  type ExpenseVersionOut,
  type RequestOut,
  type SelectionOut,
} from "./api";

export type CaseBundle = {
  request: RequestOut;
  selection: SelectionOut | null;
  agreements: AgreementOut[];
  expenses: ExpenseVersionOut[];
  completion: CompletionOut | null;
};

export async function loadCase(requestId: string): Promise<CaseBundle> {
  const request = await api<RequestOut>(`/api/requests/${requestId}`);
  const selection = await api<SelectionOut | null>(
    `/api/requests/${requestId}/selection`,
  ).catch(() => null);

  if (!selection) {
    return { request, selection: null, agreements: [], expenses: [], completion: null };
  }

  const [agreements, expenses, completion] = await Promise.all([
    api<AgreementOut[]>(`/api/selections/${selection.id}/agreements`).catch(() => []),
    api<ExpenseVersionOut[]>(`/api/selections/${selection.id}/expenses`).catch(() => []),
    api<CompletionOut | null>(`/api/selections/${selection.id}/completion`).catch(() => null),
  ]);

  return { request, selection, agreements, expenses, completion };
}

export function activeAgreement(agreements: AgreementOut[]): AgreementOut | null {
  return agreements.find((agreement) => agreement.status === "active") ?? null;
}

export function pendingAgreement(agreements: AgreementOut[]): AgreementOut | null {
  return agreements.find((agreement) => agreement.status === "proposed") ?? null;
}

/** A default appointment far enough ahead to be inside a fresh visit window. */
export function defaultScheduledAt(selection: SelectionOut | null): string {
  return selection?.scheduledAt ?? new Date(Date.now() + 86_400_000 * 2).toISOString();
}
