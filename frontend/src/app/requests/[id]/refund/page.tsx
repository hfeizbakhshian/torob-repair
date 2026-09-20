"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError, tehranTime, toman, type RefundOut, type RequestOut } from "@/lib/api";
import {
  Button,
  Card,
  Empty,
  ErrorNote,
  Field,
  InfoNote,
  SampleTag,
  Spinner,
  StatusPill,
  inputClass,
} from "@/components/ui";

const STATUS_LABELS: Record<string, string> = {
  requested: "ثبت‌شده",
  reviewing: "در حال بررسی",
  needs_review: "در انتظار بازبینی",
  approved: "استحقاق تأیید شد",
  rejected: "رد شد",
  transfer_pending: "در انتظار انتقال",
  paid: "انتقال آزمایشی انجام شد",
  transfer_failed: "انتقال ناموفق — قابل تلاش مجدد",
};

const STATUS_TONE: Record<string, "neutral" | "good" | "warn" | "bad"> = {
  paid: "good",
  approved: "good",
  rejected: "bad",
  transfer_failed: "bad",
  reviewing: "warn",
  needs_review: "warn",
};

const REASON_LABELS: Record<string, string> = {
  no_valid_offer: "تا پایان مهلت، پیشنهاد معتبری وجود نداشت",
  bootstrap_policy: "حالت bootstrap — بدون نیاز به بررسی قیمتی AI",
  price_complaint: "اعتراض قیمتی با مرجع آماده",
  review_not_completed_in_time: "بررسی در مهلت مقرر تکمیل نشد",
  abandoned_before_publish: "رهاشدن پرونده پیش از انتشار",
};

export default function RefundPage() {
  const { id: requestId } = useParams<{ id: string }>();
  const [request, setRequest] = useState<RequestOut | null>(null);
  const [refund, setRefund] = useState<RefundOut | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setRequest(await api<RequestOut>(`/api/requests/${requestId}`));
      setRefund(await api<RefundOut | null>(`/api/requests/${requestId}/refund`));
    } catch (problem) {
      setError(problem as ApiError);
    }
  }, [requestId]);

  useEffect(() => {
    void load();
  }, [load]);

  const act = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      await load();
    } catch (problem) {
      setError(problem as ApiError);
    } finally {
      setBusy(false);
    }
  };

  if (!request) {
    return error ? <ErrorNote message={error.message} fields={error.fieldErrors} /> : <Spinner />;
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-bold sm:text-2xl">بازپرداخت هزینهٔ ثبت درخواست</h1>
        <Link href={`/requests/${requestId}`} className="text-sm text-brand-600 underline">
          بازگشت به پرونده
        </Link>
      </div>

      {error && <ErrorNote message={error.message} fields={error.fieldErrors} />}

      <InfoNote>
        مبلغ واجد بازپرداخت، فقط هزینهٔ بستهٔ همین پرونده است و اجرت، قطعه و عیب‌یابی را
        شامل نمی‌شود. بررسی رایگان است و به سهمیهٔ چت وابسته نیست. در این نصب، انتقال وجه
        شبیه‌سازی می‌شود.
      </InfoNote>

      {!refund && (
        <Card title="ثبت درخواست بازپرداخت">
          <div className="flex flex-col gap-3">
            <p className="text-sm">
              سیاست این پرونده: <strong>{request.refundPolicyMode}</strong>
            </p>
            <Field label="توضیح شما (اختیاری)" htmlFor="note">
              <textarea
                id="note"
                rows={3}
                className={inputClass}
                value={note}
                onChange={(event) => setNote(event.target.value)}
              />
            </Field>
            <div>
              <Button
                onClick={() =>
                  void act(async () => {
                    await api(`/api/requests/${requestId}/refund`, {
                      method: "POST",
                      body: { note: note || null },
                    });
                  })
                }
                busy={busy}
                disabled={!request.hasPaid}
                data-testid="request-refund"
              >
                ثبت درخواست بازپرداخت
              </Button>
            </div>
            {!request.hasPaid && (
              <Empty>برای این پرونده پرداخت موفقی ثبت نشده است.</Empty>
            )}
          </div>
        </Card>
      )}

      {refund && (
        <Card
          title="وضعیت بازپرداخت"
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <SampleTag>انتقال آزمایشی</SampleTag>
              <StatusPill
                label={STATUS_LABELS[refund.status] ?? refund.status}
                tone={STATUS_TONE[refund.status] ?? "neutral"}
              />
            </div>
          }
        >
          <dl className="grid gap-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-ink-500">مبلغ</dt>
              <dd className="tabular text-base font-bold">{toman(refund.amountToman)}</dd>
            </div>
            <div>
              <dt className="text-ink-500">حالت سیاست</dt>
              <dd className="font-semibold">{refund.policyMode}</dd>
            </div>
            <div>
              <dt className="text-ink-500">علت</dt>
              <dd className="font-semibold">
                {refund.reason ? (REASON_LABELS[refund.reason] ?? refund.reason) : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-ink-500">مهلت رسیدگی</dt>
              <dd className="font-semibold">{tehranTime(refund.reviewDeadline)}</dd>
            </div>
            <div>
              <dt className="text-ink-500">تلاش‌های انتقال</dt>
              <dd className="tabular font-semibold">
                {refund.transferAttempts.toLocaleString("fa-IR")}
              </dd>
            </div>
            <div>
              <dt className="text-ink-500">زمان انتقال</dt>
              <dd className="font-semibold">{tehranTime(refund.transferredAt)}</dd>
            </div>
          </dl>

          {refund.decisionNote && (
            <p className="mt-3 rounded-lg border border-ink-200 bg-ink-50 p-3 text-sm leading-7">
              {refund.decisionNote}
            </p>
          )}

          {refund.findings.length > 0 && (
            <>
              <h3 className="mt-4 text-sm font-bold">نتیجهٔ بررسی هر پیشنهاد</h3>
              <ul className="mt-1 flex flex-col gap-2 text-sm">
                {refund.findings.map((finding, index) => (
                  <li key={index} className="rounded-lg border border-ink-200 p-2">
                    <p className="font-semibold">{String(finding.verdict)}</p>
                    <p className="text-xs text-ink-700">{String(finding.reason ?? "")}</p>
                  </li>
                ))}
              </ul>
            </>
          )}

          {refund.status === "rejected" && (
            <div className="mt-4 flex flex-col gap-3 rounded-lg border border-ink-200 bg-ink-50 p-3">
              <Field label="مدرک یا توضیح تازه" htmlFor="reopen-note">
                <textarea
                  id="reopen-note"
                  rows={2}
                  className={inputClass}
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                />
              </Field>
              <div>
                <Button
                  variant="secondary"
                  onClick={() =>
                    void act(async () => {
                      await api(`/api/refunds/${refund.id}/reopen`, {
                        method: "POST",
                        body: { note },
                      });
                    })
                  }
                  busy={busy}
                  disabled={note.trim().length === 0}
                  data-testid="reopen-refund"
                >
                  درخواست یک بازبینی با مدرک تازه
                </Button>
              </div>
              <p className="text-xs text-ink-500">
                تا ۷ روز پس از رد، یک بازبینی رایگان ممکن است. پس از آن، پیگیری از مسیر
                پشتیبانی انجام می‌شود.
              </p>
            </div>
          )}

          <p className="mt-4 text-xs text-ink-500">
            استحقاق بازپرداخت و انتقال موفق وجه دو رخداد مستقل‌اند. رسید این انتقال
            صریحاً آزمایشی است.
          </p>
        </Card>
      )}
    </div>
  );
}
