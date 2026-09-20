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

export default function MyRequestsPage() {
  const [requests, setRequests] = useState<RequestOut[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const now = new Date();

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
              </div>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
