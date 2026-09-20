"use client";

import { useCallback, useEffect, useState } from "react";
import {
  api,
  ApiError,
  tehranTime,
  toman,
  type DemoStateOut,
  type MetricsOut,
  type SupportQueueOut,
} from "@/lib/api";
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

function Ratio({
  label,
  value,
  denominator,
  suffix = "",
}: {
  label: string;
  value: number | null | undefined;
  denominator: number;
  suffix?: string;
}) {
  return (
    <div className="rounded-lg border border-ink-200 p-3">
      <p className="text-xs text-ink-500">{label}</p>
      {denominator === 0 || value === null || value === undefined ? (
        <p className="text-sm font-semibold text-ink-700">داده‌ای نیست</p>
      ) : (
        <p className="tabular text-lg font-bold">
          {value.toLocaleString("fa-IR")}
          {suffix}
        </p>
      )}
      <p className="tabular text-xs text-ink-500">
        مخرج: {denominator.toLocaleString("fa-IR")}
      </p>
    </div>
  );
}

export default function SupportPage() {
  const [queue, setQueue] = useState<SupportQueueOut | null>(null);
  const [metrics, setMetrics] = useState<MetricsOut | null>(null);
  const [demo, setDemo] = useState<DemoStateOut | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setQueue(await api<SupportQueueOut>("/api/support/queue"));
      setMetrics(await api<MetricsOut>("/api/support/metrics"));
      setDemo(await api<DemoStateOut>("/api/demo/state").catch(() => null));
    } catch (problem) {
      setError(problem as ApiError);
    }
  }, []);

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

  if (!queue) {
    return error ? <ErrorNote message={error.message} fields={error.fieldErrors} /> : <Spinner />;
  }

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-bold sm:text-2xl">میز پشتیبانی</h1>
      {error && <ErrorNote message={error.message} fields={error.fieldErrors} />}

      {demo && (
        <Card
          title="کنترل دمو"
          subtitle="فقط در نصب نمایشی. ساعت سیستم یا فایل خارج از پروژه تغییر نمی‌کند."
          actions={<SampleTag>نصب نمایشی</SampleTag>}
        >
          <p className="text-sm">
            زمان سرور: <strong>{tehranTime(demo.serverTime)}</strong> — اختلاف ساعت دمو:{" "}
            <span className="tabular">
              {Math.round(demo.clockOffsetSeconds / 3600).toLocaleString("fa-IR")} ساعت
            </span>
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {[1, 3, 25, 49].map((hours) => (
              <Button
                key={hours}
                variant="secondary"
                busy={busy}
                onClick={() =>
                  void act(async () => {
                    await api("/api/demo/advance-clock", {
                      method: "POST",
                      body: { seconds: hours * 3600 },
                    });
                  })
                }
                data-testid={`advance-${hours}h`}
              >
                +{hours.toLocaleString("fa-IR")} ساعت
              </Button>
            ))}
            <Button
              variant="secondary"
              busy={busy}
              onClick={() =>
                void act(async () => {
                  await api("/api/demo/reset-clock", { method: "POST" });
                })
              }
            >
              بازنشانی ساعت
            </Button>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <span className="text-sm">
              سیاست مبتنی بر مرجع (نمایشی):{" "}
              <strong>{demo.demoReferenceReady ? "فعال" : "غیرفعال"}</strong>
            </span>
            <Button
              variant="secondary"
              busy={busy}
              onClick={() =>
                void act(async () => {
                  await api("/api/demo/reference-ready", {
                    method: "POST",
                    body: { enabled: !demo.demoReferenceReady },
                  });
                })
              }
              data-testid="toggle-reference"
            >
              {demo.demoReferenceReady ? "بازگشت به bootstrap" : "فعال‌کردن سناریوی مرجع"}
            </Button>
            <Button
              variant="secondary"
              busy={busy}
              onClick={() =>
                void act(async () => {
                  await api("/api/demo/gateway-outcome", {
                    method: "POST",
                    body: { outcome: "fail" },
                  });
                })
              }
              data-testid="queue-gateway-failure"
            >
              شکست بعدی درگاه پرداخت
            </Button>
          </div>
          <div className="mt-3">
            <InfoNote>
              این پرچم فقط سناریوی سیاست مبتنی بر داده را در دمو فعال می‌کند. نمونه‌های
              نمایشی هرگز یک گروه مقایسهٔ واقعی را آماده نمی‌کنند.
            </InfoNote>
          </div>
        </Card>
      )}

      <Card title="صف بازپرداخت">
        {queue.refunds.length === 0 ? (
          <Empty>موردی در صف نیست.</Empty>
        ) : (
          <ul className="flex flex-col gap-2 text-sm">
            {queue.refunds.map((refund) => (
              <li key={refund.id} className="rounded-lg border border-ink-200 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="tabular font-semibold">{toman(refund.amountToman)}</span>
                  <StatusPill label={refund.status} tone="warn" />
                </div>
                <p className="text-xs text-ink-700">
                  حالت سیاست: {refund.policyMode} — مهلت: {tehranTime(refund.reviewDeadline)}
                </p>
                {refund.customerNote && (
                  <p className="mt-1 text-xs text-ink-500">{refund.customerNote}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card
        title="اختلاف‌های نیازمند رسیدگی"
        subtitle="حکم تسویهٔ هوش مصنوعی توسط پشتیبان بازنویسی نمی‌شود؛ نقش پشتیبان تکمیل شواهد است."
      >
        {queue.disputesAwaitingAi.length === 0 ? (
          <Empty>موردی در صف نیست.</Empty>
        ) : (
          <ul className="flex flex-col gap-2 text-sm">
            {queue.disputesAwaitingAi.map((dispute) => (
              <li key={dispute.id} className="rounded-lg border border-ink-200 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-semibold">پروندهٔ {dispute.id.slice(0, 8)}</span>
                  <StatusPill label={dispute.status} tone="bad" />
                </div>
                <p className="text-xs text-ink-700">
                  تعداد اقلام: {dispute.claimItems.length.toLocaleString("fa-IR")} — دور
                  تکمیل شواهد: {dispute.evidenceRoundsUsed.toLocaleString("fa-IR")}
                </p>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card
        title="منابع قیمت قطعه در انتظار تأیید"
        subtitle="صرف واردکردن عدد توسط یک طرف برای کسر امتیاز کافی نیست."
      >
        {queue.priceSourcesPending.length === 0 ? (
          <Empty>منبعی در انتظار تأیید نیست.</Empty>
        ) : (
          <ul className="flex flex-col gap-2 text-sm">
            {queue.priceSourcesPending.map((snapshot) => (
              <li key={snapshot.id} className="rounded-lg border border-ink-200 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-semibold">{snapshot.sellerName}</span>
                  <span className="tabular">{toman(snapshot.unitPriceToman)}</span>
                </div>
                <p className="text-xs text-ink-500">
                  مشاهده: {tehranTime(snapshot.observedAt)} — {snapshot.sourceLabel}
                </p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button
                    busy={busy}
                    onClick={() =>
                      void act(async () => {
                        await api(`/api/support/price-snapshots/${snapshot.id}/verify`, {
                          method: "POST",
                          body: { approve: true, reason: null },
                        });
                      })
                    }
                    data-testid={`verify-${snapshot.id}`}
                  >
                    تأیید منبع
                  </Button>
                  <Button
                    variant="danger"
                    busy={busy}
                    onClick={() =>
                      void act(async () => {
                        await api(`/api/support/price-snapshots/${snapshot.id}/verify`, {
                          method: "POST",
                          body: { approve: false, reason: "هم‌مبنا نبودن مشخصات" },
                        });
                      })
                    }
                  >
                    رد با دلیل
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="اعتراض‌های باز به ارزیابی">
        {queue.appealsOpen.length === 0 ? (
          <Empty>اعتراض بازی وجود ندارد.</Empty>
        ) : (
          <ul className="flex flex-col gap-2 text-sm">
            {queue.appealsOpen.map((appeal) => (
              <li key={String(appeal.id)} className="rounded-lg border border-ink-200 p-3">
                <p className="font-semibold">{String(appeal.reason)}</p>
                <p className="text-xs text-ink-500">
                  مهلت رسیدگی: {tehranTime(String(appeal.deadline))}
                </p>
                <div className="mt-2">
                  <Button
                    busy={busy}
                    onClick={() =>
                      void act(async () => {
                        await api(`/api/support/appeals/${String(appeal.id)}/resolve`, {
                          method: "POST",
                          body: { note: "شواهد بررسی و اصلاح شد.", newVerdicts: null },
                        });
                      })
                    }
                  >
                    ثبت نتیجهٔ رسیدگی
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {metrics && (
        <Card
          title="سنجه‌های داخل محصول"
          subtitle="کنار هر نسبت، مخرج آن نمایش داده می‌شود؛ در مخرج صفر «داده‌ای نیست» می‌آید."
          actions={<SampleTag>دادهٔ نمونه</SampleTag>}
        >
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <Ratio
              label="میانهٔ زمان تا اولین پیشنهاد (دقیقه)"
              value={metrics.firstValidOfferMedianMinutes}
              denominator={metrics.firstValidOfferCount}
            />
            <Ratio
              label="میانگین پیشنهادهای قابل مقایسه"
              value={metrics.comparableOffersAverage}
              denominator={metrics.comparableOffersDenominator}
            />
            <Ratio
              label="نسبت درخواست‌های بی‌پیشنهاد"
              value={metrics.noOfferRatio}
              denominator={metrics.noOfferDenominator}
            />
            <Ratio
              label="نرخ پرداخت پس از نمایش هزینه"
              value={metrics.paymentAfterCostShownRatio}
              denominator={metrics.paymentDenominator}
            />
            <Ratio
              label="نسبت پرونده‌های دارای علت منفی (U/N)"
              value={metrics.transparencyNegativeRatio}
              denominator={metrics.transparencyDenominator}
            />
            <Ratio
              label="میانگین رضایت مشتری"
              value={metrics.satisfactionAverage}
              denominator={metrics.satisfactionResponses}
            />
            <Ratio
              label="میانگین توکن ورودی هر پروندهٔ بسته"
              value={metrics.aiTokensAverage}
              denominator={metrics.aiDenominator}
            />
            <Ratio
              label="صدک ۹۵ توکن ورودی"
              value={metrics.aiTokensP95}
              denominator={metrics.aiDenominator}
            />
            <Ratio
              label="میانهٔ ساعت تا صدور حکم"
              value={metrics.disputeMedianHoursToDecision}
              denominator={metrics.disputeCount}
            />
            <Ratio
              label="میانهٔ ساعت تا انتقال بازپرداخت"
              value={metrics.refundMedianHoursToTransfer}
              denominator={metrics.refundCount}
            />
          </div>
          <div className="mt-3">
            <InfoNote>
              این اعداد از دادهٔ نمونهٔ همین نصب ساخته شده‌اند و تقاضای واقعی یا رضایت
              بازار را نشان نمی‌دهند.
            </InfoNote>
          </div>
        </Card>
      )}
    </div>
  );
}
