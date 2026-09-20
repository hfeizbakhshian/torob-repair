"use client";

import { useState } from "react";
import { api, ApiError, type AiRunOut } from "@/lib/api";
import { Button, Card, ErrorNote, Field, InfoNote, SampleTag, inputClass } from "@/components/ui";

type Guidance = {
  answer: string;
  basedOn: string[];
  unknowns: string[];
  needsInPersonCheck: boolean;
  suggestsAgreementChange: boolean;
};

type Entry = { question: string; guidance: Guidance; isDemo: boolean };

/**
 * Stage two of the assistant: shared by the customer and the selected specialist.
 * One quota covers both of them, so a second person does not double the allowance.
 */
export function CaseAssistant({ selectionId }: { selectionId: string }) {
  const [question, setQuestion] = useState("");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  const ask = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api<AiRunOut>(`/api/selections/${selectionId}/ai/ask`, {
        method: "POST",
        body: { question },
      });
      if (result.payload) {
        setEntries((all) => [
          ...all,
          {
            question,
            guidance: result.payload as unknown as Guidance,
            isDemo: result.isDemoResponse,
          },
        ]);
        setQuestion("");
      } else {
        setError(
          new ApiError(503, {
            code: "SERVICE_UNAVAILABLE",
            message: result.message ?? "پاسخ معتبری دریافت نشد.",
          }),
        );
      }
    } catch (problem) {
      setError(problem as ApiError);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title="پرسش دربارهٔ این همکاری"
      subtitle="سهمیهٔ این مرحله میان مشتری و متخصص منتخب مشترک است."
    >
      {error && <ErrorNote message={error.message} fields={error.fieldErrors} />}

      <div className="mt-2 flex flex-col gap-3">
        {entries.map((entry, index) => (
          <div key={index} className="rounded-lg border border-ink-200 p-3 text-sm">
            <p className="font-semibold">پرسش: {entry.question}</p>
            <p className="mt-2 leading-7">{entry.guidance.answer}</p>
            {entry.guidance.basedOn.length > 0 && (
              <p className="mt-1 text-xs text-ink-500">
                مبنای پاسخ: {entry.guidance.basedOn.join("، ")}
              </p>
            )}
            {entry.guidance.unknowns.length > 0 && (
              <ul className="mt-1 list-inside list-disc text-xs text-ink-700">
                {entry.guidance.unknowns.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            )}
            <div className="mt-2 flex flex-wrap gap-2">
              {entry.isDemo && <SampleTag>پاسخ AI نمایشی</SampleTag>}
              {entry.guidance.needsInPersonCheck && <SampleTag>نیازمند بررسی حضوری</SampleTag>}
              {entry.guidance.suggestsAgreementChange && (
                <SampleTag>نیازمند نسخهٔ تازهٔ توافق</SampleTag>
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="mt-3 flex flex-col gap-3">
        <Field
          label="پرسش شما"
          htmlFor="case-question"
          hint="پاسخ فقط سوابق همین پرونده را توضیح می‌دهد."
        >
          <textarea
            id="case-question"
            rows={2}
            className={inputClass}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
          />
        </Field>
        <div>
          <Button
            onClick={() => void ask()}
            busy={busy}
            disabled={question.trim().length < 3}
            data-testid="ask-case"
          >
            پرسیدن
          </Button>
        </div>
        <InfoNote>
          این پاسخ مبلغ توافق را تغییر نمی‌دهد و به‌جای هیچ‌کدام از طرفین چیزی را تأیید
          نمی‌کند. هر تغییر هزینه باید نسخهٔ تازه‌ای از توافق بسازد و به تأیید صریح هر دو
          طرف برسد. با پایان سهمیه، توافق و مشاهدهٔ پیشنهاد همچنان فعال می‌مانند.
        </InfoNote>
      </div>
    </Card>
  );
}
