"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError, tehranTime, toman, type AgreementOut, type LineItem } from "@/lib/api";
import { LineItems, Totals } from "@/components/line-items";
import { activeAgreement, loadCase, pendingAgreement, type CaseBundle } from "@/lib/case";
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

const STATUS_TONE: Record<string, "neutral" | "good" | "warn" | "bad"> = {
  active: "good",
  proposed: "warn",
  rejected: "bad",
  expired: "bad",
  superseded: "neutral",
  ended: "neutral",
  void: "neutral",
};

export default function AgreementPage() {
  const { id: requestId } = useParams<{ id: string }>();
  const [bundle, setBundle] = useState<CaseBundle | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [changeReason, setChangeReason] = useState("");
  const [extraTitle, setExtraTitle] = useState("");
  const [extraAmount, setExtraAmount] = useState("");
  const [evidence, setEvidence] = useState("");

  const load = useCallback(async () => {
    try {
      setBundle(await loadCase(requestId));
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

  const { selection, agreements } = bundle;
  const active = activeAgreement(agreements);
  const pending = pendingAgreement(agreements);

  const proposeChange = () =>
    act(async () => {
      if (!selection || !active) return;
      const amount = Number(extraAmount);
      const lines: LineItem[] = [
        ...active.lines,
        {
          id: `extra-${Date.now()}`,
          type: "extra",
          title: extraTitle,
          amountToman: Number.isFinite(amount) ? amount : 0,
          amountKnown: true,
          suppliedBy: "specialist",
          paidTo: "specialist",
        } as LineItem,
      ];
      await api(`/api/selections/${selection.id}/agreements`, {
        method: "POST",
        body: {
          lines,
          scenarios: [],
          scheduledAt: active.scheduledAt,
          warrantyNote: active.warrantyNote,
          changeReason,
          evidenceIds: evidence ? [evidence] : [],
        },
      });
      setExtraTitle("");
      setExtraAmount("");
      setChangeReason("");
      setEvidence("");
    });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-bold sm:text-2xl">توافق و تغییر هزینه</h1>
        <Link href={`/requests/${requestId}`} className="text-sm text-brand-600 underline">
          بازگشت به پرونده
        </Link>
      </div>

      {error && <ErrorNote message={error.message} fields={error.fieldErrors} />}

      <InfoNote>
        هر نسخهٔ توافق فقط با تأیید صریح هر دو طرف روی <strong>همان نسخه</strong> فعال
        می‌شود. سکوت یا تأیید نسخهٔ قدیمی کافی نیست و نظر هوش مصنوعی شرط آن نیست.
      </InfoNote>

      {!selection && <Empty>هنوز همکاری پذیرفته‌شده‌ای برای این پرونده وجود ندارد.</Empty>}

      {agreements.length === 0 && selection && (
        <Empty>هنوز نسخه‌ای از توافق ثبت نشده است.</Empty>
      )}

      {agreements
        .slice()
        .reverse()
        .map((agreement) => (
          <AgreementCard
            key={agreement.id}
            agreement={agreement}
            busy={busy}
            onApprove={() =>
              act(async () => {
                await api(`/api/agreements/${agreement.id}/approve`, {
                  method: "POST",
                  body: { expectedRevision: agreement.revision },
                });
              })
            }
            onReject={() =>
              act(async () => {
                await api(`/api/agreements/${agreement.id}/reject`, {
                  method: "POST",
                  body: {
                    reason: "این نسخه را نمی‌پذیرم.",
                    expectedRevision: agreement.revision,
                  },
                });
              })
            }
          />
        ))}

      {active && !pending && (
        <Card
          title="پیشنهاد تغییر هزینه"
          subtitle="هر تغییر باید دلیل و شناسهٔ مدرک داشته باشد و به تأیید دوبارهٔ هر دو طرف برسد."
        >
          <div className="flex flex-col gap-3">
            <Field label="عنوان قلم تازه" htmlFor="extra-title">
              <input
                id="extra-title"
                className={inputClass}
                value={extraTitle}
                onChange={(event) => setExtraTitle(event.target.value)}
              />
            </Field>
            <Field label="مبلغ (تومان)" htmlFor="extra-amount">
              <input
                id="extra-amount"
                type="number"
                min={0}
                className={inputClass}
                value={extraAmount}
                onChange={(event) => setExtraAmount(event.target.value)}
              />
            </Field>
            <Field
              label="دلیل تغییر"
              htmlFor="change-reason"
              hint="موافقت مشتری اجازهٔ تغییر است، ولی به‌تنهایی دلیل موجه‌بودن آن نیست."
            >
              <textarea
                id="change-reason"
                rows={2}
                className={inputClass}
                value={changeReason}
                onChange={(event) => setChangeReason(event.target.value)}
              />
            </Field>
            <Field label="شناسهٔ مدرک" htmlFor="evidence" hint="اختیاری ولی برای ارزیابی مؤثر است.">
              <input
                id="evidence"
                className={inputClass}
                value={evidence}
                onChange={(event) => setEvidence(event.target.value)}
              />
            </Field>
            <div>
              <Button
                onClick={() => void proposeChange()}
                busy={busy}
                disabled={!extraTitle || !changeReason}
                data-testid="propose-change"
              >
                ثبت نسخهٔ تازهٔ توافق
              </Button>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}

function AgreementCard({
  agreement,
  busy,
  onApprove,
  onReject,
}: {
  agreement: AgreementOut;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  const approvedBy = agreement.approvedBy;
  return (
    <Card
      title={`نسخهٔ ${agreement.versionNumber.toLocaleString("fa-IR")}`}
      subtitle={`نوبت مراجعه: ${tehranTime(agreement.scheduledAt)}`}
      actions={
        <StatusPill label={agreement.status} tone={STATUS_TONE[agreement.status] ?? "neutral"} />
      }
    >
      <LineItems lines={agreement.lines} />
      <Totals
        totalToman={agreement.totalToman}
        specialistPayableToman={agreement.specialistPayableToman}
      />

      {agreement.changeReason && (
        <p className="mt-3 text-sm">
          <span className="font-semibold">دلیل تغییر:</span> {agreement.changeReason}
        </p>
      )}
      {agreement.changeDiff?.delta !== undefined && agreement.changeDiff.delta !== null && (
        <p className="tabular text-sm text-ink-700">
          تفاوت با نسخهٔ قبل: {toman(Number(agreement.changeDiff.delta))}
        </p>
      )}
      {agreement.evidenceIds.length > 0 && (
        <p className="text-xs text-ink-500">شناسهٔ مدارک: {agreement.evidenceIds.join("، ")}</p>
      )}

      <div className="mt-3 rounded-lg border border-ink-200 bg-ink-50 p-3 text-xs leading-6">
        <p className="font-semibold">تأییدهای همین نسخه</p>
        <p>مشتری: {approvedBy.includes("customer") ? "تأیید شده" : "تأیید نشده"}</p>
        <p>متخصص: {approvedBy.includes("specialist") ? "تأیید شده" : "تأیید نشده"}</p>
        <p className="mt-1 text-ink-500">شرط انصراف: {agreement.cancellationTerms}</p>
        <p className="text-ink-500">شرط داوری: {agreement.arbitrationClause}</p>
      </div>

      {agreement.status === "proposed" && (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button onClick={onApprove} busy={busy} data-testid={`approve-${agreement.id}`}>
            تأیید همین نسخه
          </Button>
          <Button variant="danger" onClick={onReject} busy={busy}>
            رد این نسخه
          </Button>
        </div>
      )}
    </Card>
  );
}
