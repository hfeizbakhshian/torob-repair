"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  api,
  ApiError,
  tehranTime,
  toman,
  type AiRunOut,
  type ExpenseVersionOut,
  type PriceCheckOut,
} from "@/lib/api";
import { LineItems, Totals } from "@/components/line-items";
import { useSession } from "@/components/session-context";
import { activeAgreement, loadCase, type CaseBundle } from "@/lib/case";
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

const RECEIPT_TONE: Record<string, "neutral" | "good" | "warn" | "bad"> = {
  pending: "warn",
  confirmed: "good",
  rejected: "bad",
};

const RECEIPT_LABEL: Record<string, string> = {
  pending: "در انتظار تأیید مشتری",
  confirmed: "تأییدشده توسط مشتری",
  rejected: "ردشده توسط مشتری",
};

export default function CompletionPage() {
  const { id: requestId } = useParams<{ id: string }>();
  const [bundle, setBundle] = useState<CaseBundle | null>(null);
  const [checks, setChecks] = useState<PriceCheckOut[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  // Each side only sees the decisions that are theirs to make. The server enforces this
  // too; hiding the other side's buttons is so nobody is offered a choice they do not have.
  const { user } = useSession();
  const isCustomer = user?.role === "customer";
  const isSpecialist = user?.role === "specialist";
  const [expenseText, setExpenseText] = useState("");
  const [rejectReason, setRejectReason] = useState("");
  const [score, setScore] = useState("");
  const [consent, setConsent] = useState(false);

  const load = useCallback(async () => {
    try {
      setBundle(await loadCase(requestId));
      setChecks(
        await api<PriceCheckOut[]>(`/api/requests/${requestId}/price-checks`).catch(() => []),
      );
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

  if (!bundle) {
    return error ? <ErrorNote message={error.message} fields={error.fieldErrors} /> : <Spinner />;
  }

  const { selection, expenses, completion, agreements } = bundle;
  const active = activeAgreement(agreements);
  const current = expenses.length > 0 ? expenses[expenses.length - 1] : undefined;

  const extractExpenses = () =>
    act(async () => {
      if (!selection) return;
      const result = await api<AiRunOut>(
        `/api/selections/${selection.id}/ai/extract-expenses`,
        { method: "POST", body: { text: expenseText } },
      );
      const items = (result.payload?.items ?? []) as Array<Record<string, unknown>>;
      const lines = items.map((item, index) => ({
        id: `x-${index}`,
        type: item.type ?? "extra",
        title: item.title ?? `قلم ${index + 1}`,
        quantity: item.type === "labor" ? null : (item.quantity ?? 1),
        minutes: item.type === "labor" ? (item.minutes ?? 30) : null,
        amountToman: item.amountToman ?? 0,
        amountKnown: true,
        suppliedBy: "specialist",
        paidTo: "specialist",
      }));
      const version = await api<ExpenseVersionOut>(
        `/api/selections/${selection.id}/expenses`,
        { method: "POST", body: { lines, sourceText: expenseText, extractedByAi: true } },
      );
      // The specialist reviews the extracted lines before the customer ever sees them.
      await api(`/api/expense-versions/${version.id}/review-extraction`, {
        method: "POST",
        body: { lines: null },
      });
      setExpenseText("");
    });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-bold sm:text-2xl">مخارج، رسید و پایان کار</h1>
        <Link href={`/requests/${requestId}`} className="text-sm text-brand-600 underline">
          بازگشت به پرونده
        </Link>
      </div>

      {error && <ErrorNote message={error.message} fields={error.fieldErrors} />}
      {!selection && <Empty>هنوز همکاری پذیرفته‌شده‌ای وجود ندارد.</Empty>}

      {selection && isSpecialist && (
        <Card
          title="ثبت مخارج از روی متن"
          subtitle="متن شما به اقلام تبدیل می‌شود و پیش از نمایش به مشتری خودتان آن را بازبینی می‌کنید."
        >
          <Field
            label="شرح مخارج"
            htmlFor="expense-text"
            hint="هر قلم در یک خط؛ مثلاً «کیت کلاچ، ۴٬۲۰۰٬۰۰۰ تومان»."
          >
            <textarea
              id="expense-text"
              rows={4}
              className={`${inputClass} min-h-24`}
              value={expenseText}
              onChange={(event) => setExpenseText(event.target.value)}
            />
          </Field>
          <div className="mt-3">
            <Button
              onClick={() => void extractExpenses()}
              busy={busy}
              disabled={expenseText.trim().length < 3}
              data-testid="extract-expenses"
            >
              استخراج اقلام و ارسال برای مشتری
            </Button>
          </div>
        </Card>
      )}

      {expenses.map((version) => (
        <Card
          key={version.id}
          title={`نسخهٔ ${version.versionNumber.toLocaleString("fa-IR")} مخارج`}
          subtitle={
            version.submittedAt
              ? `ارسال‌شده در ${tehranTime(version.submittedAt)}`
              : "هنوز برای مشتری ارسال نشده است"
          }
          actions={
            <div className="flex flex-wrap items-center gap-2">
              {version.extractedByAi && <SampleTag>استخراج با AI</SampleTag>}
              <StatusPill
                label={RECEIPT_LABEL[version.receiptStatus] ?? version.receiptStatus}
                tone={RECEIPT_TONE[version.receiptStatus] ?? "neutral"}
              />
            </div>
          }
        >
          <LineItems lines={version.lines} />
          <Totals
            totalToman={version.totalToman}
            specialistPayableToman={version.specialistPayableToman}
          />
          {version.receiptReason && (
            <p className="mt-2 text-sm text-brand-700">دلیل رد: {version.receiptReason}</p>
          )}

          {version.receiptStatus === "pending" &&
            version.submittedAt &&
            current?.id === version.id &&
            isCustomer && (
              <div className="mt-4 flex flex-col gap-3 rounded-lg border border-ink-200 bg-ink-50 p-3">
                <p className="text-sm font-semibold">
                  تأیید یا رد این نسخه — تصمیم شما فقط به همین نسخه تعلق می‌گیرد.
                </p>
                <Field label="دلیل رد (در صورت رد)" htmlFor={`reason-${version.id}`}>
                  <input
                    id={`reason-${version.id}`}
                    className={inputClass}
                    value={rejectReason}
                    onChange={(event) => setRejectReason(event.target.value)}
                  />
                </Field>
                <div className="flex flex-wrap gap-2">
                  <Button
                    onClick={() =>
                      void act(async () => {
                        await api(`/api/expense-versions/${version.id}/review`, {
                          method: "POST",
                          body: { approve: true, reason: null },
                        });
                      })
                    }
                    busy={busy}
                    data-testid={`confirm-receipt-${version.id}`}
                  >
                    تأیید همهٔ اقلام این نسخه
                  </Button>
                  <Button
                    variant="danger"
                    onClick={() =>
                      void act(async () => {
                        await api(`/api/expense-versions/${version.id}/review`, {
                          method: "POST",
                          body: { approve: false, reason: rejectReason },
                        });
                      })
                    }
                    busy={busy}
                    disabled={rejectReason.trim().length === 0}
                  >
                    رد با دلیل
                  </Button>
                </div>
                <p className="text-xs text-ink-500">
                  تأیید رسید به‌تنهایی مجوز افزایش مبلغ توافق نیست. اگر با رسید موافق
                  نیستید، رد کنید تا مسیر اصلاح یا اختلاف باز شود.
                </p>
              </div>
            )}
        </Card>
      ))}

      {checks.length > 0 && (
        <Card
          title="مقایسهٔ قیمت فاکتور قطعه با ترب"
          subtitle="مبنای این مقایسه، قیمت‌های هم‌مبنای ثبت‌شده و تأییدشدهٔ منبع است."
        >
          <ul className="flex flex-col gap-2 text-sm">
            {checks.map((check) => (
              <li key={check.id} className="rounded-lg border border-ink-200 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-semibold">قلم: {check.lineId}</span>
                  <StatusPill
                    label={check.displayLabel}
                    tone={
                      check.verdict === "overpriced" && check.established
                        ? "bad"
                        : check.verdict === "within_range"
                          ? "good"
                          : "neutral"
                    }
                  />
                </div>
                <p className="tabular mt-1 text-xs text-ink-700">
                  قیمت واحد فاکتور: {toman(check.invoiceUnitPriceToman)} — میانهٔ ترب:{" "}
                  {toman(check.medianReferenceToman)}
                  {check.ratio !== null && check.ratio !== undefined
                    ? ` — نسبت: ${check.ratio.toLocaleString("fa-IR")}`
                    : ""}
                </p>
                {check.equivalenceNote && (
                  <p className="mt-1 text-xs text-ink-500">{check.equivalenceNote}</p>
                )}
                {!check.established && check.verdict !== "not_assessable" && (
                  <p className="mt-1 text-xs text-amber-800">
                    مبنای این هشدار هنوز قطعی نشده است؛ تا تأیید رسید یا احراز در داوری،
                    علت منفی برای متخصص ثبت نمی‌شود.
                  </p>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {selection && (
        <Card title="پایان کار">
          {active && (
            <p className="mb-3 text-sm">
              مبلغ توافق فعال:{" "}
              <span className="tabular font-bold">{toman(active.totalToman)}</span> — قابل
              پرداخت به متخصص:{" "}
              <span className="tabular font-bold">{toman(active.specialistPayableToman)}</span>
            </p>
          )}

          {completion?.requestedAt ? (
            <div className="flex flex-col gap-3">
              <p className="text-sm">
                صورت‌حساب ثبت‌شده:{" "}
                <span className="tabular font-bold">
                  {toman(completion.invoiceTotalToman)}
                </span>
              </p>
              {completion.customerConfirmedAt ? (
                <InfoNote>
                  پایان کار در {tehranTime(completion.customerConfirmedAt)} تأیید شد.
                </InfoNote>
              ) : !isCustomer ? (
                <InfoNote>
                  صورت‌حساب برای مشتری ارسال شده و در انتظار تأیید اوست. بی‌پاسخی، تأیید
                  محسوب نمی‌شود.
                </InfoNote>
              ) : (
                <>
                  <Field label="امتیاز رضایت (۱ تا ۵)" htmlFor="score">
                    <input
                      id="score"
                      type="number"
                      min={1}
                      max={5}
                      className={inputClass}
                      value={score}
                      onChange={(event) => setScore(event.target.value)}
                    />
                  </Field>
                  <label className="flex items-start gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={consent}
                      onChange={(event) => setConsent(event.target.checked)}
                    />
                    <span>
                      با استفادهٔ بی‌نام از این پرونده برای ساخت مرجع قیمت موافقم.
                    </span>
                  </label>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      onClick={() =>
                        void act(async () => {
                          await api(
                            `/api/selections/${selection.id}/completion/confirm`,
                            {
                              method: "POST",
                              body: {
                                satisfactionScore: score ? Number(score) : null,
                                satisfactionNote: null,
                                referenceConsent: consent,
                                expectedRevision: completion.revision,
                              },
                            },
                          );
                        })
                      }
                      busy={busy}
                      data-testid="confirm-completion"
                    >
                      تأیید صورت‌حساب و پایان کار
                    </Button>
                    <Button
                      variant="danger"
                      onClick={() =>
                        void act(async () => {
                          await api(
                            `/api/selections/${selection.id}/completion/mismatch`,
                            { method: "POST", body: { reason: "صورت‌حساب با توافق مغایرت دارد." } },
                          );
                        })
                      }
                      busy={busy}
                    >
                      گزارش مغایرت
                    </Button>
                  </div>
                  <p className="text-xs text-ink-500">
                    تأیید عادی فقط وقتی ممکن است که صورت‌حساب با توافق فعال منطبق و رسید
                    مبنا توسط شما تأیید شده باشد. در غیر این صورت مسیر اختلاف باز می‌شود.
                  </p>
                </>
              )}
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              <InfoNote>
                متخصص پس از ثبت مخارج، پایان کار را درخواست می‌کند. بی‌پاسخی هیچ‌کدام از
                طرفین، پایان موفق یا تأیید هزینه محسوب نمی‌شود.
              </InfoNote>
              <div hidden={!isSpecialist}>
                <Button
                  variant="secondary"
                  onClick={() =>
                    void act(async () => {
                      await api(`/api/selections/${selection.id}/completion`, {
                        method: "POST",
                      });
                    })
                  }
                  busy={busy}
                  disabled={!current?.submittedAt}
                  data-testid="request-completion"
                >
                  درخواست پایان کار
                </Button>
              </div>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
