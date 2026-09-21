"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  OFFER_STATUS_LABELS,
  OFFER_TYPE_LABELS,
  REQUEST_STATUS_LABELS,
  api,
  ApiError,
  relativeDeadline,
  tehranTime,
  toman,
  type OfferOut,
  type RequestOut,
  type ServiceTemplateOut,
  type SelectionOut,
} from "@/lib/api";
import { CaseAssistant } from "@/components/case-assistant";
import { LineItems } from "@/components/line-items";
import {
  Button,
  Card,
  Empty,
  ErrorNote,
  Field,
  InfoNote,
  Spinner,
  StatusPill,
  inputClass,
} from "@/components/ui";

type Part = { title: string; toman: string };

type Draft = {
  offerType: string;
  parts: Part[];
  diagnosticFee: string;
  diagnosticScope: string;
  minutes: string;
  hourlyRate: string;
  scheduledAt: string;
  warranty: string;
};

function defaultDraft(request: RequestOut): Draft {
  const start = request.visitWindowStart ? new Date(request.visitWindowStart) : new Date();
  const scheduled = new Date(start.getTime() + 3_600_000 * 24);
  return {
    // The service decides which kinds of offer it accepts; the form follows it.
    offerType: request.version?.allowedOfferTypes?.[0] ?? "fixed",
    parts: [{ title: "کیت کلاچ کامل", toman: "4200000" }],
    diagnosticFee: "900000",
    diagnosticScope: "بررسی و تعیین علت؛ تعمیر یا تعویض قطعه جداگانه توافق می‌شود.",
    minutes: "180",
    hourlyRate: "400000",
    scheduledAt: scheduled.toISOString().slice(0, 16),
    warranty: "ضمانت نمونهٔ ۶ ماهه",
  };
}

export default function SpecialistPage() {
  const [requests, setRequests] = useState<RequestOut[] | null>(null);
  const [cases, setCases] = useState<RequestOut[]>([]);
  const [services, setServices] = useState<Record<string, string>>({});
  const [offers, setOffers] = useState<OfferOut[]>([]);
  const [selections, setSelections] = useState<Record<string, SelectionOut | null>>({});
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const now = new Date();

  const load = useCallback(async () => {
    try {
      const catalogue = await api<ServiceTemplateOut[]>("/api/catalog/services");
      setServices(Object.fromEntries(catalogue.map((item) => [item.code, item.titleFa])));
      const list = await api<RequestOut[]>("/api/specialist/requests");
      setRequests(list);
      // A case leaves the open feed once it is assigned, so it is fetched separately.
      const mineCases = await api<RequestOut[]>("/api/specialist/cases");
      setCases(mineCases);
      setOffers(await api<OfferOut[]>("/api/specialist/offers"));
      const found: Record<string, SelectionOut | null> = {};
      await Promise.all(
        [...list, ...mineCases].map(async (request) => {
          found[request.id] = await api<SelectionOut | null>(
            `/api/requests/${request.id}/selection`,
          ).catch(() => null);
        }),
      );
      setSelections(found);
    } catch (problem) {
      setError(problem as ApiError);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const timer = setInterval(() => void load(), 15_000);
    return () => clearInterval(timer);
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

  const submitOffer = (request: RequestOut) =>
    act(async () => {
      const draft = drafts[request.id] ?? defaultDraft(request);
      const minutes = Number(draft.minutes);
      const rate = Number(draft.hourlyRate);
      const laborAmount = Math.round((minutes * rate) / 60);
      const deadline = request.responseDeadline ? new Date(request.responseDeadline) : new Date();
      const usable = draft.parts.filter((row) => row.title.trim() && Number(row.toman) > 0);
      const part = usable.reduce((sum, row) => sum + Number(row.toman), 0);
      const offerType = draft.offerType;
      const partLines = usable.map((row, index) => ({
        id: `part-${index + 1}`,
        type: "part",
        title: row.title,
        quantity: 1,
        unitRateToman: Number(row.toman),
        amountToman: Number(row.toman),
        amountKnown: true,
        suppliedBy: "specialist",
        paidTo: "specialist",
        // A conditional offer prices the parts only in the scenario that needs them.
        ...(offerType === "conditional" ? { scenarioCode: "with_part" } : {}),
      }));
      const laborLine = {
        id: "labor-1",
        type: "labor",
        title: offerType === "diagnostic" ? "اجرت عیب‌یابی" : "اجرت انجام کار",
        operationCode: "main",
        minutes,
        hourlyRateToman: rate,
        amountToman: offerType === "diagnostic" ? Number(draft.diagnosticFee) : laborAmount,
        amountKnown: true,
        suppliedBy: "specialist",
        paidTo: "specialist",
      };
      await api(`/api/requests/${request.id}/offers`, {
        method: "POST",
        body: {
          offerType,
          lines: offerType === "diagnostic" ? [laborLine] : [...partLines, laborLine],
          scenarios:
            offerType === "conditional"
              ? [
                  {
                    code: "labor_only",
                    title: "بدون تعویض قطعه",
                    totalToman: laborAmount,
                  },
                  {
                    code: "with_part",
                    title:
                      usable.length === 1
                        ? `با تعویض ${usable[0]?.title ?? "قطعه"}`
                        : `با تعویض ${usable.length.toLocaleString("fa-IR")} قطعه`,
                    totalToman: part + laborAmount,
                  },
                ]
              : [],
          conditionsNote:
            offerType === "conditional"
              ? "مبلغ نهایی پس از بازدید و بر اساس سناریوی محقق‌شده تعیین می‌شود."
              : null,
          diagnosticFeeToman:
            offerType === "diagnostic" ? Number(draft.diagnosticFee) : null,
          diagnosticScope: offerType === "diagnostic" ? draft.diagnosticScope : null,
          scheduledAt: new Date(draft.scheduledAt).toISOString(),
          validUntil: new Date(deadline.getTime() + 3_600_000 * 48).toISOString(),
          estimatedMinutes: minutes,
          warrantyNote: draft.warranty,
          diagnosticFeeCredited: false,
        },
      });
    });

  if (requests === null) {
    return error ? <ErrorNote message={error.message} fields={error.fieldErrors} /> : <Spinner />;
  }

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-bold sm:text-2xl">میز کار متخصص</h1>
      {error && <ErrorNote message={error.message} fields={error.fieldErrors} />}

      <InfoNote>
        پیش از انتخاب مشتری، فقط خلاصهٔ فنی، محله و بازهٔ زمانی و پیشنهاد خودتان را
        می‌بینید. پیشنهاد رقبا، تماس و مدارک خصوصی در دسترس شما نیست. قیمت آزاد است؛ فقط
        نقص ساختاری مانع ارسال می‌شود.
      </InfoNote>

      <Card title="درخواست‌های مرتبط با شما">
        {requests.length === 0 ? (
          <Empty>در حال حاضر درخواست مرتبطی وجود ندارد.</Empty>
        ) : (
          <div className="flex flex-col gap-4">
            {requests.map((request) => {
              const draft = drafts[request.id] ?? defaultDraft(request);
              // Only an offer on *this* request counts as already submitted.
              const mine = offers.find((offer) => offer.requestId === request.id);
              const selection = selections[request.id] ?? null;
              return (
                <div key={request.id} className="rounded-lg border border-ink-200 p-3">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <p className="font-bold">
                        {services[request.version?.serviceCode ?? ""] ??
                          request.version?.serviceCode}
                      </p>
                      <p className="text-xs text-ink-500">
                        {request.version?.city} — محلهٔ {request.version?.district} —{" "}
                        {request.version?.vehicleCode}
                      </p>
                    </div>
                    <div className="text-left">
                      <StatusPill
                        label={REQUEST_STATUS_LABELS[request.status] ?? request.status}
                      />
                      <p className="mt-1 text-xs text-ink-500">
                        {relativeDeadline(request.responseDeadline, now)}
                      </p>
                    </div>
                  </div>

                  <details className="mt-2">
                    <summary className="cursor-pointer text-sm font-medium">
                      خلاصهٔ فنی درخواست
                    </summary>
                    <ul className="mt-1 list-inside list-disc text-sm leading-7">
                      {request.version?.summaryFacts.map((fact) => <li key={fact}>{fact}</li>)}
                    </ul>
                    <p className="mt-1 text-xs text-ink-500">
                      نوع پیشنهاد مجاز: {request.version?.allowedOfferTypes.join("، ")}
                    </p>
                    <p className="text-xs text-ink-500">
                      بازهٔ مراجعه: {tehranTime(request.visitWindowStart)} تا{" "}
                      {tehranTime(request.visitWindowEnd)}
                    </p>
                  </details>

                  {selection && selection.status === "pending" && (
                    <div className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3">
                      <p className="text-sm font-semibold text-amber-900">
                        مشتری شما را انتخاب کرده است — مهلت پاسخ:{" "}
                        {relativeDeadline(selection.acceptanceDeadline, now)}
                      </p>
                      <p className="mt-1 text-xs text-amber-900">
                        با پذیرش، شرط داوری لازم‌الاجرای هوش مصنوعی را نیز می‌پذیرید.
                        پذیرش انتخاب به‌تنهایی مجوز تعمیر نیست.
                      </p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        <Button
                          onClick={() =>
                            void act(async () => {
                              await api(`/api/selections/${selection.id}/accept`, {
                                method: "POST",
                                body: {
                                  acceptedArbitration: true,
                                  expectedRevision: selection.revision,
                                },
                              });
                            })
                          }
                          busy={busy}
                          data-testid={`accept-${selection.id}`}
                        >
                          پذیرش همکاری
                        </Button>
                        <Button
                          variant="danger"
                          onClick={() =>
                            void act(async () => {
                              await api(`/api/selections/${selection.id}/reject`, {
                                method: "POST",
                                body: {
                                  cannotPerform: true,
                                  reason: "امکان انجام این کار را ندارم.",
                                  expectedRevision: selection.revision,
                                },
                              });
                            })
                          }
                          busy={busy}
                        >
                          رد انتخاب
                        </Button>
                      </div>
                    </div>
                  )}

                  {request.status === "open" && !mine && (
                    <details className="mt-3">
                      <summary className="cursor-pointer text-sm font-medium">
                        ثبت یا ویرایش پیشنهاد
                      </summary>
                      {(request.version?.allowedOfferTypes?.length ?? 0) > 1 && (
                        <div className="mt-2">
                          <Field label="نوع پیشنهاد" htmlFor={`ot-${request.id}`}>
                            <select
                              id={`ot-${request.id}`}
                              className={inputClass}
                              value={draft.offerType}
                              onChange={(event) =>
                                setDrafts({
                                  ...drafts,
                                  [request.id]: { ...draft, offerType: event.target.value },
                                })
                              }
                            >
                              {request.version?.allowedOfferTypes?.map((kind) => (
                                <option key={kind} value={kind}>
                                  {OFFER_TYPE_LABELS[kind] ?? kind}
                                </option>
                              ))}
                            </select>
                          </Field>
                        </div>
                      )}

                      {draft.offerType === "diagnostic" && (
                        <div className="mt-2 flex flex-col gap-3">
                          <InfoNote>
                            این درخواست فقط پیشنهاد عیب‌یابی می‌پذیرد: هزینه و دامنهٔ
                            بررسی، بدون قطعه. تعمیر یا تعویض قطعه پس از بررسی جداگانه
                            توافق می‌شود و این مبلغ، قیمت تعمیر کامل نیست.
                          </InfoNote>
                          <Field label="هزینهٔ عیب‌یابی (تومان)" htmlFor={`df-${request.id}`}>
                            <input
                              id={`df-${request.id}`}
                              type="number"
                              className={inputClass}
                              value={draft.diagnosticFee}
                              onChange={(event) =>
                                setDrafts({
                                  ...drafts,
                                  [request.id]: { ...draft, diagnosticFee: event.target.value },
                                })
                              }
                            />
                          </Field>
                          <Field label="دامنهٔ بررسی" htmlFor={`ds-${request.id}`}>
                            <textarea
                              id={`ds-${request.id}`}
                              rows={2}
                              className={inputClass}
                              value={draft.diagnosticScope}
                              onChange={(event) =>
                                setDrafts({
                                  ...drafts,
                                  [request.id]: { ...draft, diagnosticScope: event.target.value },
                                })
                              }
                            />
                          </Field>
                        </div>
                      )}

                      {draft.offerType !== "diagnostic" && (
                        <div className="mt-2 flex flex-col gap-2">
                          <p className="text-sm font-medium">قطعات</p>
                          {draft.parts.map((row, index) => (
                            <div key={index} className="grid gap-2 sm:grid-cols-[1fr_10rem_auto]">
                              <input
                                aria-label={`عنوان قطعهٔ ${index + 1}`}
                                className={inputClass}
                                placeholder="عنوان قطعه"
                                value={row.title}
                                onChange={(event) => {
                                  const parts = draft.parts.map((item, at) =>
                                    at === index ? { ...item, title: event.target.value } : item,
                                  );
                                  setDrafts({ ...drafts, [request.id]: { ...draft, parts } });
                                }}
                              />
                              <input
                                aria-label={`قیمت قطعهٔ ${index + 1}`}
                                type="number"
                                className={inputClass}
                                placeholder="تومان"
                                value={row.toman}
                                onChange={(event) => {
                                  const parts = draft.parts.map((item, at) =>
                                    at === index ? { ...item, toman: event.target.value } : item,
                                  );
                                  setDrafts({ ...drafts, [request.id]: { ...draft, parts } });
                                }}
                              />
                              <button
                                type="button"
                                className="min-h-11 rounded-lg border border-ink-200 px-3 text-sm text-ink-600 hover:bg-ink-100 disabled:opacity-40"
                                disabled={draft.parts.length === 1}
                                onClick={() => {
                                  const parts = draft.parts.filter((_, at) => at !== index);
                                  setDrafts({ ...drafts, [request.id]: { ...draft, parts } });
                                }}
                              >
                                حذف
                              </button>
                            </div>
                          ))}
                          <div>
                            <button
                              type="button"
                              className="min-h-9 rounded-md border border-ink-200 px-3 py-1 text-xs font-semibold hover:bg-ink-100"
                              onClick={() =>
                                setDrafts({
                                  ...drafts,
                                  [request.id]: {
                                    ...draft,
                                    parts: [...draft.parts, { title: "", toman: "" }],
                                  },
                                })
                              }
                            >
                              افزودن قطعه
                            </button>
                          </div>
                        </div>
                      )}

                      <div className="mt-2 grid gap-3 sm:grid-cols-2">
                        <Field label="زمان کار (دقیقه)" htmlFor={`m-${request.id}`}>
                          <input
                            id={`m-${request.id}`}
                            type="number"
                            className={inputClass}
                            value={draft.minutes}
                            onChange={(event) =>
                              setDrafts({
                                ...drafts,
                                [request.id]: { ...draft, minutes: event.target.value },
                              })
                            }
                          />
                        </Field>
                        <Field label="نرخ ساعتی (تومان)" htmlFor={`r-${request.id}`}>
                          <input
                            id={`r-${request.id}`}
                            type="number"
                            className={inputClass}
                            value={draft.hourlyRate}
                            onChange={(event) =>
                              setDrafts({
                                ...drafts,
                                [request.id]: { ...draft, hourlyRate: event.target.value },
                              })
                            }
                          />
                        </Field>
                        <Field
                          label="زمان مراجعهٔ پیشنهادی"
                          htmlFor={`s-${request.id}`}
                          hint="باید داخل بازهٔ مراجعه و در آینده باشد."
                        >
                          <input
                            id={`s-${request.id}`}
                            type="datetime-local"
                            className={inputClass}
                            value={draft.scheduledAt}
                            onChange={(event) =>
                              setDrafts({
                                ...drafts,
                                [request.id]: { ...draft, scheduledAt: event.target.value },
                              })
                            }
                          />
                        </Field>
                        <Field label="ضمانت" htmlFor={`w-${request.id}`}>
                          <input
                            id={`w-${request.id}`}
                            className={inputClass}
                            value={draft.warranty}
                            onChange={(event) =>
                              setDrafts({
                                ...drafts,
                                [request.id]: { ...draft, warranty: event.target.value },
                              })
                            }
                          />
                        </Field>
                      </div>
                      <p className="tabular mt-2 text-sm">
                        مبلغ کل تقریبی:{" "}
                        <strong>
                          {toman(
                            draft.offerType === "diagnostic"
                              ? Number(draft.diagnosticFee || 0)
                              : draft.parts.reduce((sum, row) => sum + Number(row.toman || 0), 0) +
                                Math.round(
                                  (Number(draft.minutes) * Number(draft.hourlyRate)) / 60,
                                ),
                          )}
                        </strong>
                      </p>
                      <div className="mt-3">
                        <Button
                          onClick={() => void submitOffer(request)}
                          busy={busy}
                          data-testid={`submit-offer-${request.id}`}
                        >
                          ثبت پیشنهاد
                        </Button>
                      </div>
                    </details>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>

      <Card title="پرونده‌های من">
        {cases.length === 0 ? (
          <Empty>هنوز پرونده‌ای به شما سپرده نشده است.</Empty>
        ) : (
          <div className="flex flex-col gap-4">
            {cases.map((request) => {
              const selection = selections[request.id] ?? null;
              const version = request.version;
              return (
                <div key={request.id} className="rounded-lg border border-ink-200 p-3">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <p className="font-bold">
                        {services[version?.serviceCode ?? ""] ?? version?.serviceCode}
                      </p>
                      <p className="text-xs text-ink-500">
                        {version?.city} — {version?.district}
                      </p>
                    </div>
                    <div className="flex flex-col items-start gap-1 sm:items-end">
                      <StatusPill
                        label={REQUEST_STATUS_LABELS[request.status] ?? request.status}
                      />
                      {selection?.scheduledAt && (
                        <span className="text-xs text-ink-500">
                          نوبت: {tehranTime(selection.scheduledAt)}
                        </span>
                      )}
                    </div>
                  </div>

                  {(version?.summaryFacts?.length || version?.summaryUnknowns?.length) && (
                    <details className="mt-3" open>
                      <summary className="cursor-pointer text-sm font-medium">
                        گزارش هوش مصنوعی از این درخواست
                      </summary>
                      <div className="mt-2 grid gap-3 sm:grid-cols-2">
                        <div>
                          <p className="text-xs font-medium text-ink-500">آنچه معلوم است</p>
                          <ul className="mt-1 list-inside list-disc text-sm">
                            {version?.summaryFacts?.map((fact, index) => (
                              <li key={index}>{fact}</li>
                            ))}
                          </ul>
                        </div>
                        <div>
                          <p className="text-xs font-medium text-ink-500">آنچه نامعلوم است</p>
                          <ul className="mt-1 list-inside list-disc text-sm">
                            {version?.summaryUnknowns?.length ? (
                              version.summaryUnknowns.map((item, index) => (
                                <li key={index}>{item}</li>
                              ))
                            ) : (
                              <li className="list-none text-ink-500">موردی ثبت نشده است.</li>
                            )}
                          </ul>
                        </div>
                      </div>
                      <p className="mt-2 text-xs text-ink-500">
                        این خلاصه از پاسخ‌های مشتری ساخته شده و تشخیص قطعی نیست.
                      </p>
                    </details>
                  )}

                  {selection?.status === "accepted" && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Link
                        href={`/requests/${request.id}/agreement`}
                        className="inline-flex min-h-11 items-center rounded-lg border border-ink-200 bg-white px-4 py-2 text-sm font-semibold hover:bg-ink-100"
                      >
                        توافق
                      </Link>
                      <Link
                        href={`/requests/${request.id}/completion`}
                        className="inline-flex min-h-11 items-center rounded-lg border border-ink-200 bg-white px-4 py-2 text-sm font-semibold hover:bg-ink-100"
                      >
                        مخارج و فاکتور نهایی
                      </Link>
                      {!selection.workStartedAt &&
                        (new Date(selection.scheduledAt) > now ? (
                          <span className="self-center text-xs text-ink-500">
                            شروع کار از {tehranTime(selection.scheduledAt)} ممکن است
                          </span>
                        ) : (
                          <Button
                            variant="secondary"
                            onClick={() =>
                              void act(async () => {
                                await api(`/api/selections/${selection.id}/start`, {
                                  method: "POST",
                                  body: { expectedRevision: selection.revision },
                                });
                              })
                            }
                            busy={busy}
                            data-testid={`start-${selection.id}`}
                          >
                            ثبت شروع کار
                          </Button>
                        ))}
                    </div>
                  )}

                  {selection?.status === "accepted" && (
                    <div className="mt-3">
                      <CaseAssistant selectionId={selection.id} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>

      <Card title="پیشنهادهای من">
        {offers.length === 0 ? (
          <Empty>هنوز پیشنهادی ثبت نکرده‌اید.</Empty>
        ) : (
          <div className="flex flex-col gap-3">
            {offers.map((offer) => (
              <div key={offer.id} className="rounded-lg border border-ink-200 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-semibold">
                    {OFFER_TYPE_LABELS[offer.version.offerType] ?? offer.version.offerType} —
                    نسخهٔ {offer.version.versionNumber.toLocaleString("fa-IR")}
                  </span>
                  <StatusPill label={OFFER_STATUS_LABELS[offer.status] ?? offer.status} />
                </div>
                <p className="tabular mt-1 text-sm">
                  مبلغ کل: {toman(offer.version.totalToman)} — نوبت:{" "}
                  {tehranTime(offer.version.scheduledAt)}
                </p>
                <details className="mt-2">
                  <summary className="cursor-pointer text-sm">ریز اقلام</summary>
                  <div className="mt-2">
                    <LineItems lines={offer.version.lines} />
                  </div>
                </details>
                {offer.status === "active" && (
                  <div className="mt-2">
                    <Button
                      variant="secondary"
                      onClick={() =>
                        void act(async () => {
                          await api(
                            `/api/offers/${offer.id}/withdraw?expectedRevision=${offer.revision}`,
                            { method: "POST" },
                          );
                        })
                      }
                      busy={busy}
                    >
                      پس‌گرفتن پیشنهاد
                    </Button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
