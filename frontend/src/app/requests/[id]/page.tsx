"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  OFFER_TYPE_LABELS,
  REQUEST_STATUS_LABELS,
  api,
  ApiError,
  relativeDeadline,
  tehranTime,
  toman,
  type AiUsageOut,
  type OfferComparisonOut,
  type OfferOut,
  type RefundOut,
  type RequestOut,
  type SelectionOut,
} from "@/lib/api";
import { LineItems } from "@/components/line-items";
import { ScoreBadge } from "@/components/score-badge";
import {
  Button,
  Card,
  Empty,
  ErrorNote,
  InfoNote,
  SampleTag,
  Spinner,
  StatusPill,
} from "@/components/ui";

type Sort = "price" | "time" | "score";

const SORT_LABELS: Record<Sort, string> = {
  price: "ارزان‌ترین",
  time: "زودترین نوبت",
  score: "بالاترین امتیاز شفافیت",
};

export default function RequestDetailPage() {
  const params = useParams<{ id: string }>();
  const requestId = params.id;

  const [request, setRequest] = useState<RequestOut | null>(null);
  const [comparison, setComparison] = useState<OfferComparisonOut | null>(null);
  const [selection, setSelection] = useState<SelectionOut | null>(null);
  const [refund, setRefund] = useState<RefundOut | null>(null);
  const [usage, setUsage] = useState<AiUsageOut | null>(null);
  const [sort, setSort] = useState<Sort>("price");
  const [acceptedTerms, setAcceptedTerms] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const now = new Date();

  const load = useCallback(async () => {
    try {
      const [detail, sel, ref] = await Promise.all([
        api<RequestOut>(`/api/requests/${requestId}`),
        api<SelectionOut | null>(`/api/requests/${requestId}/selection`).catch(() => null),
        api<RefundOut | null>(`/api/requests/${requestId}/refund`).catch(() => null),
      ]);
      setRequest(detail);
      setSelection(sel);
      setRefund(ref);
      // A customer sees the comparison; a specialist on this case does not.
      setComparison(
        await api<OfferComparisonOut>(
          `/api/requests/${requestId}/offers?sort=${sort}`,
        ).catch(() => null),
      );
      setUsage(await api<AiUsageOut>(`/api/requests/${requestId}/ai/usage`).catch(() => null));
    } catch (problem) {
      setError(problem as ApiError);
    }
  }, [requestId, sort]);

  useEffect(() => {
    void load();
  }, [load]);

  // Polling keeps the dashboard current; nothing is pushed outside the system.
  useEffect(() => {
    const timer = setInterval(() => void load(), 15_000);
    return () => clearInterval(timer);
  }, [load]);

  const selectOffer = async (offer: OfferOut) => {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/requests/${requestId}/selection`, {
        method: "POST",
        body: {
          offerVersionId: offer.version.id,
          acceptedArbitration: acceptedTerms,
          expectedRevision: request?.revision ?? null,
        },
      });
      await load();
    } catch (problem) {
      setError(problem as ApiError);
    } finally {
      setBusy(false);
    }
  };

  const askRefund = async () => {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/requests/${requestId}/refund`, {
        method: "POST",
        body: { note: "درخواست بازپرداخت هزینهٔ ثبت درخواست" },
      });
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
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold sm:text-2xl">
            {request.version?.serviceCode} — {request.version?.city}
          </h1>
          <p className="mt-1 text-sm text-ink-500">
            محله: {request.version?.district} — خودرو: {request.version?.vehicleCode}
          </p>
        </div>
        <StatusPill label={REQUEST_STATUS_LABELS[request.status] ?? request.status} />
      </div>

      {error && <ErrorNote message={error.message} fields={error.fieldErrors} />}

      <Card title="مرحله و مهلت‌ها">
        <dl className="grid gap-3 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-ink-500">مهلت دریافت پیشنهاد</dt>
            <dd className="font-semibold">
              {relativeDeadline(request.responseDeadline, now)}
              <span className="block text-xs font-normal text-ink-500">
                {tehranTime(request.responseDeadline)}
              </span>
            </dd>
          </div>
          <div>
            <dt className="text-ink-500">بازهٔ مراجعه</dt>
            <dd className="font-semibold">
              {tehranTime(request.visitWindowStart)} تا {tehranTime(request.visitWindowEnd)}
            </dd>
          </div>
          <div>
            <dt className="text-ink-500">دریافت پیشنهاد</dt>
            <dd className="font-semibold">
              {request.offersClosedAt ? "بسته شده" : "باز"}
            </dd>
          </div>
        </dl>
      </Card>

      {request.version && (
        <Card title="خلاصهٔ تأییدشدهٔ درخواست">
          <ul className="list-inside list-disc text-sm leading-7">
            {request.version.summaryFacts.map((fact) => (
              <li key={fact}>{fact}</li>
            ))}
          </ul>
          {request.version.summaryUnknowns.length > 0 && (
            <>
              <p className="mt-3 text-sm font-bold">موارد نامعلوم</p>
              <ul className="list-inside list-disc text-sm leading-7 text-ink-700">
                {request.version.summaryUnknowns.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </>
          )}
          <p className="mt-3 text-xs text-ink-500">
            نوع پیشنهاد مجاز: {request.version.allowedOfferTypes.join("، ")}
          </p>
        </Card>
      )}

      {selection && (
        <Card title="همکاری انتخاب‌شده">
          <dl className="grid gap-3 text-sm sm:grid-cols-3">
            <div>
              <dt className="text-ink-500">وضعیت</dt>
              <dd className="font-semibold">{selection.status}</dd>
            </div>
            <div>
              <dt className="text-ink-500">مهلت پذیرش متخصص</dt>
              <dd className="font-semibold">
                {relativeDeadline(selection.acceptanceDeadline, now)}
              </dd>
            </div>
            <div>
              <dt className="text-ink-500">نوبت مراجعه</dt>
              <dd className="font-semibold">{tehranTime(selection.scheduledAt)}</dd>
            </div>
          </dl>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link
              href={`/requests/${requestId}/agreement`}
              className="inline-flex min-h-11 items-center rounded-lg border border-ink-200 bg-white px-4 py-2 text-sm font-semibold hover:bg-ink-100"
            >
              توافق و تغییر هزینه
            </Link>
            <Link
              href={`/requests/${requestId}/parts`}
              className="inline-flex min-h-11 items-center rounded-lg border border-ink-200 bg-white px-4 py-2 text-sm font-semibold hover:bg-ink-100"
            >
              قطعه و لینک ترب
            </Link>
            <Link
              href={`/requests/${requestId}/completion`}
              className="inline-flex min-h-11 items-center rounded-lg border border-ink-200 bg-white px-4 py-2 text-sm font-semibold hover:bg-ink-100"
            >
              مخارج، رسید و پایان
            </Link>
            <Link
              href={`/requests/${requestId}/dispute`}
              className="inline-flex min-h-11 items-center rounded-lg border border-ink-200 bg-white px-4 py-2 text-sm font-semibold hover:bg-ink-100"
            >
              اختلاف و داوری
            </Link>
          </div>
        </Card>
      )}

      {comparison && (
        <Card
          title="مقایسهٔ پیشنهادها"
          subtitle="ترتیب پایدار است و به ترتیب رسیدن پیشنهادها بستگی ندارد."
          actions={
            <div className="flex flex-wrap gap-1">
              {(Object.keys(SORT_LABELS) as Sort[]).map((key) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setSort(key)}
                  aria-pressed={sort === key}
                  className={`min-h-9 rounded-lg border px-3 py-1.5 text-xs font-medium ${
                    sort === key
                      ? "border-brand-500 bg-brand-50 text-brand-700"
                      : "border-ink-200 bg-white hover:bg-ink-100"
                  }`}
                >
                  {SORT_LABELS[key]}
                </button>
              ))}
            </div>
          }
        >
          {comparison.scenarioGroups.every((group) => group.offers.length === 0) ? (
            <Empty>
              هنوز پیشنهادی ثبت نشده است. تا پایان مهلت ۲۴ ساعته این درخواست باز می‌ماند.
            </Empty>
          ) : (
            <>
              <label className="mb-4 flex items-start gap-2 rounded-lg border border-ink-200 bg-ink-50 p-3 text-sm">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={acceptedTerms}
                  onChange={(event) => setAcceptedTerms(event.target.checked)}
                  data-testid="accept-arbitration"
                />
                <span>
                  شرط داوری را می‌پذیرم: اگر اختلاف با توافق حل نشود، حکم هوش مصنوعی
                  دربارهٔ اقلام و مبلغ این پرونده لازم‌الاجرا ثبت و ابلاغ می‌شود. در این
                  نسخه اجرای حکم یعنی ثبت نتیجه؛ وصول وجه تعمیر انجام نمی‌شود.
                </span>
              </label>

              <div className="flex flex-col gap-5">
                {comparison.scenarioGroups.map((group) => (
                  <div key={group.scenarioCode ?? "none"}>
                    <h3 className="mb-2 text-sm font-bold">{group.title}</h3>
                    {group.note && <InfoNote tone="warn">{group.note}</InfoNote>}
                    <div className="mt-2 grid gap-3">
                      {group.offers.map((offer) => (
                        <div
                          key={offer.id}
                          className="rounded-lg border border-ink-200 p-3"
                          data-testid={`offer-${offer.id}`}
                        >
                          <div className="flex flex-wrap items-start justify-between gap-3">
                            <div>
                              <p className="font-bold">{offer.specialist.shopName}</p>
                              <p className="text-xs text-ink-500">
                                {offer.specialist.displayName} — محلهٔ{" "}
                                {offer.specialist.district}
                              </p>
                              <p className="mt-1 text-xs">
                                نوع پیشنهاد:{" "}
                                {OFFER_TYPE_LABELS[offer.version.offerType] ??
                                  offer.version.offerType}
                              </p>
                            </div>
                            <div className="text-left">
                              <p className="tabular text-lg font-bold">
                                {toman(offer.scenarioTotalToman ?? offer.version.totalToman)}
                              </p>
                              {offer.scenarioTotalToman !== null &&
                                offer.scenarioTotalToman !== undefined && (
                                  <p className="text-xs text-ink-500">برای همین سناریو</p>
                                )}
                              <p className="text-xs text-ink-500">
                                نوبت: {tehranTime(offer.version.scheduledAt)}
                              </p>
                              <p className="text-xs text-ink-500">
                                اعتبار تا: {tehranTime(offer.version.validUntil)}
                              </p>
                            </div>
                          </div>

                          {offer.version.offerType === "diagnostic" && (
                            <div className="mt-2">
                              <InfoNote tone="warn">
                                این مبلغ فقط هزینهٔ عیب‌یابی است و قیمت تعمیر کامل نیست.
                                دامنه: {offer.version.diagnosticScope}
                              </InfoNote>
                            </div>
                          )}

                          <details className="mt-3">
                            <summary className="cursor-pointer text-sm font-medium">
                              ریز اقلام و شرایط
                            </summary>
                            <div className="mt-2">
                              <LineItems lines={offer.version.lines} />
                              {offer.version.warrantyNote && (
                                <p className="mt-2 text-xs text-ink-700">
                                  ضمانت: {offer.version.warrantyNote}
                                </p>
                              )}
                              <div className="mt-3">
                                <ScoreBadge specialist={offer.specialist} />
                              </div>
                            </div>
                          </details>

                          <div className="mt-3 flex flex-wrap items-center gap-2">
                            <Button
                              onClick={() => void selectOffer(offer)}
                              disabled={!offer.selectable || !acceptedTerms || busy}
                              data-testid={`select-${offer.id}`}
                            >
                              انتخاب این متخصص
                            </Button>
                            {!offer.selectable && (
                              <span className="text-xs text-brand-700">
                                ⚠ {offer.notSelectableReason}
                              </span>
                            )}
                            {!acceptedTerms && offer.selectable && (
                              <span className="text-xs text-ink-500">
                                برای انتخاب، ابتدا شرط داوری را بپذیرید.
                              </span>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </Card>
      )}

      <Card
        title="بازپرداخت هزینهٔ ثبت درخواست"
        subtitle="بررسی رایگان است و به سهمیهٔ چت وابسته نیست."
        actions={<SampleTag>انتقال آزمایشی</SampleTag>}
      >
        {refund ? (
          <div className="text-sm leading-7">
            <p>
              وضعیت: <strong>{refund.status}</strong> — مبلغ:{" "}
              <span className="tabular">{toman(refund.amountToman)}</span>
            </p>
            {refund.reason && <p>علت: {refund.reason}</p>}
            {refund.decisionNote && <p className="text-ink-700">{refund.decisionNote}</p>}
            <Link
              href={`/requests/${requestId}/refund`}
              className="mt-2 inline-block text-brand-600 underline"
            >
              مشاهدهٔ جزئیات بازپرداخت
            </Link>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            <InfoNote>
              سیاست این پرونده: <strong>{request.refundPolicyMode}</strong>. در حالت
              bootstrap، درخواست شما بدون بررسی قیمتی AI پذیرفته می‌شود.
            </InfoNote>
            <div>
              <Button
                variant="secondary"
                onClick={() => void askRefund()}
                busy={busy}
                disabled={!request.hasPaid}
                data-testid="ask-refund"
              >
                درخواست بازپرداخت
              </Button>
            </div>
          </div>
        )}
      </Card>

      {usage && (
        <Card title="مصرف دستیار هوشمند" actions={usage.isDemo ? <SampleTag>AI نمایشی</SampleTag> : undefined}>
          <p className="mb-3 text-sm text-ink-700">{usage.note}</p>
          <ul className="grid gap-2 text-sm sm:grid-cols-2">
            {usage.stages.map((stage) => (
              <li key={stage.stage} className="rounded-lg border border-ink-200 p-2">
                <p className="font-semibold">{stage.stage}</p>
                <p className="tabular text-xs text-ink-700">
                  نوبت: {stage.turnsUsed.toLocaleString("fa-IR")} از{" "}
                  {stage.maxTurns.toLocaleString("fa-IR")}
                </p>
                <p className="tabular text-xs text-ink-700">
                  ورودی: {stage.inputTokensUsed.toLocaleString("fa-IR")} از{" "}
                  {stage.inputTokensLimit.toLocaleString("fa-IR")}
                </p>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-ink-500">
            با پایان سهمیه، توافق، مشاهدهٔ پیشنهاد و درخواست بازپرداخت همچنان فعال می‌مانند.
          </p>
        </Card>
      )}
    </div>
  );
}
