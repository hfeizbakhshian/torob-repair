"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  REQUEST_STATUS_LABELS,
  api,
  ApiError,
  relativeDeadline,
  tehranTime,
  type RequestOut,
} from "@/lib/api";
import { Card, Empty, ErrorNote, Spinner, StatusPill } from "@/components/ui";

/** Mirrors DELETABLE_STATUSES on the server; a live case keeps a specialist's work. */
const DELETABLE = new Set([
  "draft",
  "cancelled",
  "closed_unselected",
  "completed",
  "closed_settled",
  "closed_adjudicated",
]);

export default function MyRequestsPage() {
  const [requests, setRequests] = useState<RequestOut[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const now = new Date();

  async function remove(request: RequestOut) {
    setDeleting(request.id);
    setError(null);
    try {
      await api<void>(`/api/requests/${request.id}?expected_revision=${request.revision}`, {
        method: "DELETE",
      });
      setRequests((rows) => (rows ?? []).filter((row) => row.id !== request.id));
      setConfirming(null);
    } catch (problem) {
      setError(problem as ApiError);
    } finally {
      setDeleting(null);
    }
  }

  useEffect(() => {
    api<RequestOut[]>("/api/requests")
      .then(setRequests)
      .catch((problem: ApiError) => setError(problem));
  }, []);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-bold sm:text-2xl">پرونده‌های من</h1>
        <Link
          href="/requests/new"
          className="inline-flex min-h-11 items-center rounded-lg bg-brand-500 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-600"
        >
          درخواست تازه
        </Link>
      </div>

      {error && <ErrorNote message={error.message} fields={error.fieldErrors} />}
      {requests === null && !error && <Spinner />}
      {requests?.length === 0 && <Empty>هنوز درخواستی ثبت نکرده‌اید.</Empty>}

      <div className="grid gap-3">
        {requests?.map((request) => (
          <Card key={request.id}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <Link href={`/requests/${request.id}`} className="text-base font-bold hover:text-brand-600">
                  {request.version?.serviceCode ?? "درخواست"}
                </Link>
                <p className="mt-1 text-sm text-ink-500">
                  {request.version?.city} — {request.version?.district}
                </p>
                <p className="mt-1 text-xs text-ink-500">
                  ثبت: {tehranTime(request.publishedAt)}
                </p>
              </div>
              <div className="flex flex-col items-start gap-2 sm:items-end">
                <StatusPill label={REQUEST_STATUS_LABELS[request.status] ?? request.status} />
                {request.responseDeadline && (
                  <span className="text-xs text-ink-500">
                    مهلت پیشنهاد: {relativeDeadline(request.responseDeadline, now)}
                  </span>
                )}
                {DELETABLE.has(request.status) &&
                  (confirming === request.id ? (
                    <span className="flex items-center gap-2 text-xs">
                      <span className="text-ink-500">حذف شود؟</span>
                      <button
                        type="button"
                        onClick={() => void remove(request)}
                        disabled={deleting === request.id}
                        className="min-h-8 rounded-md bg-red-600 px-2 py-1 font-semibold text-white hover:bg-red-700 disabled:opacity-60"
                      >
                        {deleting === request.id ? "در حال حذف…" : "بله، حذف کن"}
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirming(null)}
                        className="min-h-8 rounded-md border border-ink-200 px-2 py-1 text-ink-600 hover:bg-ink-50"
                      >
                        انصراف
                      </button>
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setConfirming(request.id)}
                      className="min-h-8 text-xs text-ink-500 underline hover:text-red-600"
                    >
                      حذف از فهرست
                    </button>
                  ))}
              </div>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
