"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  api,
  ApiError,
  toman,
  type AiRunOut,
  type CoverageResult,
  type PolicySummary,
  type RequestOut,
  type ServiceTemplateOut,
} from "@/lib/api";
import {
  Button,
  Card,
  ErrorNote,
  Field,
  InfoNote,
  SampleTag,
  Spinner,
  inputClass,
} from "@/components/ui";

const CITY = "تهران";
const VEHICLE = "peugeot_206_type_5";
const VEHICLE_LABEL = "پژو ۲۰۶ تیپ ۵";

type Question = {
  id: string;
  text: string;
  type: "single_choice" | "multi_choice" | "short_text" | "number";
  options: string[];
  required: boolean;
};

type Summary = {
  facts: string[];
  unknowns: string[];
  suggestedOfferType: string;
  needsInPersonCheck: boolean;
  note: string | null;
};

type Step = "describe" | "pay" | "questions" | "summary";

export default function NewRequestPage() {
  const router = useRouter();
  // Read on the client rather than through useSearchParams, which would force this whole
  // page behind a Suspense boundary just to pick up an optional query parameter.
  const [resumeId, setResumeId] = useState<string | null>(null);
  useEffect(() => {
    setResumeId(new URLSearchParams(window.location.search).get("resume"));
  }, []);
  const [services, setServices] = useState<ServiceTemplateOut[] | null>(null);
  const [serviceCode, setServiceCode] = useState("");
  const [district, setDistrict] = useState("تهرانسر");
  const [symptoms, setSymptoms] = useState("");
  const [coverage, setCoverage] = useState<CoverageResult | null>(null);
  const [terms, setTerms] = useState<PolicySummary | null>(null);
  const [request, setRequest] = useState<RequestOut | null>(null);
  const [questions, setQuestions] = useState<Question[] | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [summary, setSummary] = useState<Summary | null>(null);
  const [isDemoAnswer, setIsDemoAnswer] = useState(true);
  const [step, setStep] = useState<Step>("describe");
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<ServiceTemplateOut[]>("/api/catalog/services")
      .then((list) => {
        setServices(list);
        setServiceCode(list[0]?.code ?? "");
      })
      .catch((problem: ApiError) => setError(problem));
  }, []);

  // Coverage and the package terms are free and involve no model call, so they are
  // checked before the payment screen is ever shown.
  useEffect(() => {
    if (!serviceCode) return;
    const body = { city: CITY, vehicleCode: VEHICLE, serviceCode, visitMode: "shop" };
    api<CoverageResult>("/api/catalog/coverage", { method: "POST", body })
      .then(setCoverage)
      .catch(() => setCoverage(null));
    api<PolicySummary>("/api/catalog/package-terms", { method: "POST", body })
      .then(setTerms)
      .catch(() => setTerms(null));
  }, [serviceCode]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (problem) {
      setError(problem as ApiError);
    } finally {
      setBusy(false);
    }
  };

  const createDraft = () =>
    run(async () => {
      const created = await api<RequestOut>("/api/requests", {
        method: "POST",
        body: {
          city: CITY,
          district,
          vehicleCode: VEHICLE,
          vehicleDetails: { label: VEHICLE_LABEL },
          serviceCode,
          symptoms,
          visitMode: "shop",
        },
      });
      setRequest(created);
      setStep("pay");
    });

  const pay = () =>
    run(async () => {
      if (!request) return;
      const key = `pay-${request.id}`;
      await api("/api/requests/" + request.id + "/pay", {
        method: "POST",
        body: { idempotencyKey: key },
        idempotencyKey: key,
      });
      setStep("questions");
      setAsking(true);
      try {
        const result = await api<AiRunOut>(`/api/requests/${request.id}/ai/questions`, {
          method: "POST",
        });
        setIsDemoAnswer(result.isDemoResponse);
        const asked = (result.payload?.questions ?? []) as Question[];
        if (asked.length === 0) {
          throw new ApiError(0, {
            code: "SERVICE_UNAVAILABLE",
            message:
              result.message ??
              "پرسشی از مدل دریافت نشد. پیش‌نویس شما محفوظ است و می‌توانید دوباره تلاش کنید.",
          });
        }
        setQuestions(asked);
      } finally {
        setAsking(false);
      }
    });

  const buildSummary = () =>
    run(async () => {
      if (!request) return;
      const result = await api<AiRunOut>(`/api/requests/${request.id}/ai/summary`, {
        method: "POST",
        body: { answers },
      });
      setIsDemoAnswer(result.isDemoResponse);
      setSummary((result.payload ?? null) as Summary | null);
      setRequest(await api<RequestOut>(`/api/requests/${request.id}`));
      setStep("summary");
    });

  const confirmAndPublish = () =>
    run(async () => {
      if (!request) return;
      // Confirming the summary is a standalone button and consumes no AI quota.
      const confirmed = await api<RequestOut>(
        `/api/requests/${request.id}/confirm-summary`,
        { method: "POST", body: { expectedRevision: request.revision } },
      );
      const published = await api<RequestOut>(`/api/requests/${request.id}/publish`, {
        method: "POST",
        body: { expectedRevision: confirmed.revision },
      });
      router.push(`/requests/${published.id}`);
    });

  // Picking a half-finished draft back up: the questions were already asked and paid
  // for, so they are read back rather than asked again.
  useEffect(() => {
    if (!resumeId) return;
    let cancelled = false;
    void (async () => {
      try {
        const existing = await api<RequestOut>(`/api/requests/${resumeId}`);
        if (cancelled || existing.status !== "draft") return;
        setRequest(existing);
        const asked = await api<AiRunOut | null>(
          `/api/requests/${resumeId}/ai/questions`,
        ).catch(() => null);
        if (cancelled) return;
        const list = (asked?.payload?.questions ?? []) as Question[];
        if (list.length > 0) {
          setQuestions(list);
          setStep("questions");
        } else {
          setStep("pay");
        }
      } catch (problem) {
        if (!cancelled) setError(problem as ApiError);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [resumeId]);

  const canDescribe = serviceCode !== "" && symptoms.trim().length > 4 && coverage?.supported;
  const selectedService = useMemo(
    () => services?.find((service) => service.code === serviceCode) ?? null,
    [services, serviceCode],
  );

  return (
    <div className="flex max-w-3xl flex-col gap-4">
      <h1 className="text-xl font-bold sm:text-2xl">ثبت درخواست تعمیر</h1>

      {error && <ErrorNote message={error.message} fields={error.fieldErrors} />}

      <Card title="۱. مشخصات و پوشش" subtitle="این مرحله رایگان است و فراخوانی AI ندارد.">
        {services === null ? (
          <Spinner />
        ) : (
          <div className="flex flex-col gap-4">
            <Field label="شهر" htmlFor="city" hint="پوشش این دمو فقط تهران است.">
              <input id="city" className={inputClass} value={CITY} readOnly />
            </Field>
            <Field label="خودرو" htmlFor="vehicle" hint="پوشش این دمو فقط پژو ۲۰۶ تیپ ۵ است.">
              <input id="vehicle" className={inputClass} value={VEHICLE_LABEL} readOnly />
            </Field>
            <Field label="محله" htmlFor="district" hint="نزدیکی محله فقط عامل مقایسه است.">
              <input
                id="district"
                className={inputClass}
                value={district}
                onChange={(event) => setDistrict(event.target.value)}
              />
            </Field>
            <Field label="خدمت احتمالی" htmlFor="service">
              <select
                id="service"
                className={inputClass}
                value={serviceCode}
                onChange={(event) => setServiceCode(event.target.value)}
                disabled={step !== "describe"}
              >
                {services.map((service) => (
                  <option key={service.code} value={service.code}>
                    {service.titleFa}
                  </option>
                ))}
              </select>
            </Field>
            {selectedService && (
              <InfoNote>
                {selectedService.summaryFa}
                {selectedService.referenceLaborMinutes !== null && (
                  <>
                    {" "}
                    زمان مرجع نمونه: {selectedService.referenceLaborMinutes.toLocaleString("fa-IR")}{" "}
                    دقیقه ({selectedService.referenceLaborSource}).
                  </>
                )}
              </InfoNote>
            )}
            <Field
              label="نشانه‌هایی که دیده‌اید"
              htmlFor="symptoms"
              hint="هرچه دقیق‌تر بنویسید، پرسش‌های کمتری لازم می‌شود."
            >
              <textarea
                id="symptoms"
                rows={4}
                className={`${inputClass} min-h-24`}
                value={symptoms}
                onChange={(event) => setSymptoms(event.target.value)}
                disabled={step !== "describe"}
              />
            </Field>

            {coverage && !coverage.supported && (
              <InfoNote tone="warn">{coverage.message}</InfoNote>
            )}

            {step === "describe" && (
              <div>
                <Button
                  onClick={() => void createDraft()}
                  disabled={!canDescribe}
                  busy={busy}
                  data-testid="create-draft"
                >
                  ادامه و مشاهدهٔ هزینه
                </Button>
              </div>
            )}
          </div>
        )}
      </Card>

      {step !== "describe" && terms && (
        <Card
          title="۲. هزینهٔ بستهٔ ثبت درخواست"
          subtitle="پیش از اولین فراخوانی AI، مبلغ و سیاست بازپرداخت را می‌بینید."
          actions={<SampleTag>پرداخت آزمایشی</SampleTag>}
        >
          <dl className="grid gap-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-ink-500">مبلغ بسته</dt>
              <dd className="tabular text-lg font-bold">{toman(terms.registrationFeeToman)}</dd>
            </div>
            <div>
              <dt className="text-ink-500">سقف نوبت هر مرحله</dt>
              <dd className="tabular font-semibold">
                {terms.maxTurnsPerStage.toLocaleString("fa-IR")} نوبت
              </dd>
            </div>
            <div>
              <dt className="text-ink-500">مهلت دریافت پیشنهاد</dt>
              <dd className="tabular font-semibold">
                {terms.offerWindowHours.toLocaleString("fa-IR")} ساعت
              </dd>
            </div>
            <div>
              <dt className="text-ink-500">حالت سیاست بازپرداخت</dt>
              <dd className="font-semibold">{terms.refundPolicyMode}</dd>
            </div>
          </dl>
          <div className="mt-3">
            <InfoNote>{terms.refundSummary}</InfoNote>
          </div>
          <p className="mt-2 text-xs text-ink-500">{terms.feeLabel}</p>
          {step === "pay" && (
            <div className="mt-4">
              <Button onClick={() => void pay()} busy={busy} data-testid="pay">
                پرداخت آزمایشی و شروع پرسش‌ها
              </Button>
            </div>
          )}
        </Card>
      )}

      {asking && (
        <Card title="۳. پرسش‌های لازم">
          <Spinner />
          <p className="mt-2 text-center text-sm text-ink-500">
            در حال آماده‌سازی پرسش‌ها با هوش مصنوعی — معمولاً چند ثانیه طول می‌کشد. این
            صفحه را نبندید؛ پیش‌نویس شما ثبت شده و در «پرونده‌های من» هست.
          </p>
        </Card>
      )}

      {questions !== null && (
        <Card
          title="۳. پرسش‌های لازم"
          subtitle="پرسش‌ها یکجا پرسیده می‌شوند تا نوبت اضافه مصرف نشود."
          actions={isDemoAnswer ? <SampleTag>پاسخ AI نمایشی</SampleTag> : undefined}
        >
          <div className="flex flex-col gap-4">
            {questions.map((question) => (
              <Field key={question.id} label={question.text} htmlFor={`q-${question.id}`}>
                {question.options.length > 0 ? (
                  <select
                    id={`q-${question.id}`}
                    className={inputClass}
                    value={answers[question.id] ?? ""}
                    onChange={(event) =>
                      setAnswers((all) => ({ ...all, [question.id]: event.target.value }))
                    }
                  >
                    <option value="">انتخاب کنید…</option>
                    {question.options.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    id={`q-${question.id}`}
                    type={question.type === "number" ? "number" : "text"}
                    className={inputClass}
                    value={answers[question.id] ?? ""}
                    onChange={(event) =>
                      setAnswers((all) => ({ ...all, [question.id]: event.target.value }))
                    }
                  />
                )}
              </Field>
            ))}
            {step === "questions" && (
              <div>
                <Button onClick={() => void buildSummary()} busy={busy} data-testid="build-summary">
                  ساخت خلاصهٔ درخواست
                </Button>
              </div>
            )}
          </div>
        </Card>
      )}

      {summary && request && (
        <Card
          title="۴. تأیید خلاصه و انتشار"
          subtitle="تأیید خلاصه دکمهٔ مستقل است و سهمیهٔ AI مصرف نمی‌کند."
          actions={isDemoAnswer ? <SampleTag>پاسخ AI نمایشی</SampleTag> : undefined}
        >
          <h3 className="text-sm font-bold">آنچه معلوم است</h3>
          <ul className="mt-1 list-inside list-disc text-sm leading-7">
            {summary.facts.map((fact) => (
              <li key={fact}>{fact}</li>
            ))}
          </ul>
          {summary.unknowns.length > 0 && (
            <>
              <h3 className="mt-3 text-sm font-bold">آنچه هنوز نامعلوم است</h3>
              <ul className="mt-1 list-inside list-disc text-sm leading-7 text-ink-700">
                {summary.unknowns.map((unknown) => (
                  <li key={unknown}>{unknown}</li>
                ))}
              </ul>
            </>
          )}
          {summary.needsInPersonCheck && (
            <div className="mt-3">
              <InfoNote tone="warn">
                اطلاعات برای تشخیص قطعی کافی نیست؛ این درخواست به بررسی حضوری (عیب‌یابی)
                هدایت می‌شود و قیمت تعمیر کامل ساخته نمی‌شود.
              </InfoNote>
            </div>
          )}
          <div className="mt-3">
            <InfoNote>
              نوع پیشنهاد مجاز برای این درخواست:{" "}
              <strong>{request.version?.allowedOfferTypes.join("، ")}</strong>
            </InfoNote>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button onClick={() => void confirmAndPublish()} busy={busy} data-testid="publish">
              تأیید خلاصه و انتشار برای متخصصان
            </Button>
          </div>
        </Card>
      )}
    </div>
  );
}
