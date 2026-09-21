"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  api,
  ApiError,
  relativeDeadline,
  tehranTime,
  toman,
  type DisputeOut,
} from "@/lib/api";
import { LineItems } from "@/components/line-items";
import { useSession } from "@/components/session-context";
import { activeAgreement, loadCase, type CaseBundle } from "@/lib/case";
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

const STATUS_LABELS: Record<string, string> = {
  dispute_open: "باز — فرصت اظهارات و توافق",
  reviewing: "در حال داوری",
  needs_evidence: "نیازمند تکمیل شواهد",
  awaiting_ai: "در انتظار رسیدگی فنی",
  resolved_by_ai: "حل‌شده با حکم داوری",
  resolved_by_agreement: "حل‌شده با توافق طرفین",
};

const STATUS_TONE: Record<string, "neutral" | "good" | "warn" | "bad"> = {
  dispute_open: "warn",
  reviewing: "warn",
  needs_evidence: "warn",
  awaiting_ai: "bad",
  resolved_by_ai: "good",
  resolved_by_agreement: "good",
};

export default function DisputePage() {
  const { id: requestId } = useParams<{ id: string }>();
  const [bundle, setBundle] = useState<CaseBundle | null>(null);
  const [dispute, setDispute] = useState<DisputeOut | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  // Both sides of a case are parties here; support reads the file but is not one of them.
  const { user } = useSession();
  const isParty = user?.role === "customer" || user?.role === "specialist";
  const [claimTitle, setClaimTitle] = useState("");
  const [claimAmount, setClaimAmount] = useState("");
  const [claimReason, setClaimReason] = useState("");
  const [statement, setStatement] = useState("");
  const now = new Date();

  const load = useCallback(async () => {
    try {
      setBundle(await loadCase(requestId));
      setDispute(await api<DisputeOut | null>(`/api/requests/${requestId}/dispute`));
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

  const active = activeAgreement(bundle.agreements);

  const openDispute = () =>
    act(async () => {
      const amount = Number(claimAmount);
      await api(`/api/requests/${requestId}/dispute`, {
        method: "POST",
        body: {
          claimItems: [
            {
              claimItemId: `claim-${Date.now()}`,
              title: claimTitle,
              type: "extra",
              claimedQuantity: 1,
              claimedAmountToman: Number.isFinite(amount) ? amount : 0,
              reason: claimReason,
              evidenceIds: [],
              suppliedBy: "specialist",
              paidTo: "specialist",
            },
          ],
        },
      });
      setClaimTitle("");
      setClaimAmount("");
      setClaimReason("");
    });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-bold sm:text-2xl">اختلاف و داوری</h1>
        <Link href={`/requests/${requestId}`} className="text-sm text-brand-600 underline">
          بازگشت به پرونده
        </Link>
      </div>

      {error && <ErrorNote message={error.message} fields={error.fieldErrors} />}

      <InfoNote>
        ابتدا امکان اصلاح و توافق وجود دارد. اگر اختلاف با توافق حل نشود، هوش مصنوعی
        براساس سوابق و اظهارات طرفین حکم می‌دهد و حکم معتبر بدون تأیید مجدد طرفین اعمال و
        ابلاغ می‌شود. در این نسخه اجرای حکم یعنی ثبت نتیجه و مبلغ تسویه؛ وصول وجه تعمیر
        انجام نمی‌شود.
      </InfoNote>

      {!dispute && !bundle.selection && (
        <Empty>اختلاف فقط دربارهٔ همکاری پذیرفته‌شده ثبت می‌شود.</Empty>
      )}

      {!dispute && bundle.selection && (
        <Card title="ثبت اختلاف">
          <div className="flex flex-col gap-3">
            <Field label="عنوان قلم مورد اختلاف" htmlFor="claim-title">
              <input
                id="claim-title"
                className={inputClass}
                value={claimTitle}
                onChange={(event) => setClaimTitle(event.target.value)}
              />
            </Field>
            <Field label="مبلغ مورد ادعا (تومان)" htmlFor="claim-amount">
              <input
                id="claim-amount"
                type="number"
                min={0}
                className={inputClass}
                value={claimAmount}
                onChange={(event) => setClaimAmount(event.target.value)}
              />
            </Field>
            <Field label="دلیل" htmlFor="claim-reason">
              <textarea
                id="claim-reason"
                rows={3}
                className={inputClass}
                value={claimReason}
                onChange={(event) => setClaimReason(event.target.value)}
              />
            </Field>
            <div>
              <Button
                onClick={() => void openDispute()}
                busy={busy}
                disabled={!claimTitle || !claimReason}
                data-testid="open-dispute"
              >
                ثبت اختلاف
              </Button>
            </div>
            <p className="text-xs text-ink-500">
              ثبت ادعا به‌تنهایی بدهی قطعی یا پرداخت ایجاد نمی‌کند؛ مبلغ نهایی از توافق
              حل اختلاف یا حکم داوری به دست می‌آید.
            </p>
          </div>
        </Card>
      )}

      {dispute && (
        <>
          <Card
            title="وضعیت اختلاف"
            actions={
              <StatusPill
                label={STATUS_LABELS[dispute.status] ?? dispute.status}
                tone={STATUS_TONE[dispute.status] ?? "neutral"}
              />
            }
          >
            <dl className="grid gap-3 text-sm sm:grid-cols-3">
              <div>
                <dt className="text-ink-500">مهلت اظهارات</dt>
                <dd className="font-semibold">
                  {relativeDeadline(dispute.statementDeadline, now)}
                  <span className="block text-xs font-normal text-ink-500">
                    {tehranTime(dispute.statementDeadline)}
                  </span>
                </dd>
              </div>
              <div>
                <dt className="text-ink-500">پایان اظهارات مشتری</dt>
                <dd className="font-semibold">
                  {dispute.customerClosedStatementsAt ? "اعلام شده" : "اعلام نشده"}
                </dd>
              </div>
              <div>
                <dt className="text-ink-500">پایان اظهارات متخصص</dt>
                <dd className="font-semibold">
                  {dispute.specialistClosedStatementsAt ? "اعلام شده" : "اعلام نشده"}
                </dd>
              </div>
            </dl>

            <h3 className="mt-4 text-sm font-bold">اقلام مورد اختلاف</h3>
            <ul className="mt-1 flex flex-col gap-2 text-sm">
              {dispute.claimItems.map((item, index) => (
                <li key={index} className="rounded-lg border border-ink-200 p-2">
                  <p className="font-semibold">{String(item.title ?? item.claimItemId)}</p>
                  <p className="tabular text-xs text-ink-700">
                    مبلغ ادعا: {toman(Number(item.claimedAmountToman ?? 0))}
                  </p>
                  <p className="text-xs text-ink-500">{String(item.reason ?? "")}</p>
                </li>
              ))}
            </ul>
          </Card>

          <Card title="اظهارات طرفین">
            {dispute.statements.length === 0 ? (
              <Empty>هنوز اظهاری ثبت نشده است.</Empty>
            ) : (
              <ul className="flex flex-col gap-2 text-sm">
                {dispute.statements.map((item, index) => (
                  <li key={index} className="rounded-lg border border-ink-200 p-3">
                    <p className="text-xs font-bold text-ink-500">
                      {item.party === "customer" ? "مشتری" : "متخصص"} — نسخهٔ{" "}
                      {String(item.versionNumber)}
                    </p>
                    <p className="mt-1 leading-7">{String(item.body)}</p>
                  </li>
                ))}
              </ul>
            )}

            {["dispute_open", "needs_evidence"].includes(dispute.status) && isParty && (
              <div className="mt-4 flex flex-col gap-3 rounded-lg border border-ink-200 bg-ink-50 p-3">
                <Field label="اظهارات شما" htmlFor="statement">
                  <textarea
                    id="statement"
                    rows={3}
                    className={inputClass}
                    value={statement}
                    onChange={(event) => setStatement(event.target.value)}
                  />
                </Field>
                <div className="flex flex-wrap gap-2">
                  <Button
                    onClick={() =>
                      void act(async () => {
                        await api(`/api/disputes/${dispute.id}/statements`, {
                          method: "POST",
                          body: { body: statement, itemPositions: [], evidenceIds: [] },
                        });
                        setStatement("");
                      })
                    }
                    busy={busy}
                    disabled={statement.trim().length === 0}
                    data-testid="submit-statement"
                  >
                    ثبت اظهارات
                  </Button>
                  <Button
                    variant="secondary"
                    onClick={() =>
                      void act(async () => {
                        await api(`/api/disputes/${dispute.id}/close-statements`, {
                          method: "POST",
                        });
                      })
                    }
                    busy={busy}
                    data-testid="close-statements"
                  >
                    اعلام پایان اظهارات من
                  </Button>
                </div>
                <p className="text-xs text-ink-500">
                  ویرایش اظهارات، ورودی قفل‌شدهٔ داوری را منقضی می‌کند تا پاسخ دیررس مدل
                  روی دادهٔ قدیمی اعمال نشود.
                </p>
              </div>
            )}
          </Card>

          <Card title="پیشنهاد حل اختلاف (توافق داوطلبانه)">
            {dispute.currentProposal ? (
              <div className="text-sm">
                <p className="tabular">
                  مبلغ پیشنهادی:{" "}
                  <strong>{toman(Number(dispute.currentProposal.totalToman ?? 0))}</strong>
                </p>
                <p className="mt-1 text-xs text-ink-700">
                  تأیید مشتری: {dispute.currentProposal.customerApprovedAt ? "بله" : "خیر"} —
                  تأیید متخصص: {dispute.currentProposal.specialistApprovedAt ? "بله" : "خیر"}
                </p>
                {!dispute.resolvedAt && (
                  <div className="mt-3">
                    <Button
                      onClick={() =>
                        void act(async () => {
                          await api(
                            `/api/dispute-proposals/${String(dispute.currentProposal?.id)}/approve`,
                            { method: "POST", body: { expectedRevision: dispute.revision } },
                          );
                        })
                      }
                      busy={busy}
                      data-testid="approve-proposal"
                    >
                      تأیید همین نسخهٔ پیشنهاد
                    </Button>
                  </div>
                )}
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                <p className="text-sm text-ink-700">
                  می‌توانید بر اساس اقلام توافق فعال، پیشنهاد حل اختلاف ثبت کنید. فقط با
                  تأیید هر دو طرف روی همان نسخه اعمال می‌شود؛ سکوت تأیید نیست.
                </p>
                <div>
                  <Button
                    variant="secondary"
                    disabled={!active || Boolean(dispute.resolvedAt)}
                    onClick={() =>
                      void act(async () => {
                        if (!active) return;
                        await api(`/api/disputes/${dispute.id}/proposals`, {
                          method: "POST",
                          body: {
                            lines: active.lines,
                            note: "بازگشت به اقلام توافق فعال",
                          },
                        });
                      })
                    }
                    busy={busy}
                    data-testid="propose-resolution"
                  >
                    پیشنهاد تسویه بر مبنای توافق فعال
                  </Button>
                </div>
              </div>
            )}
          </Card>

          {!dispute.resolvedAt && (
            <Card title="ورود به داوری">
              <p className="text-sm text-ink-700">
                پس از پایان فرصت اظهارات یا اعلام پایان اظهارات هر دو طرف، داوری آغاز
                می‌شود. بی‌پاسخی طرف مقابل ثبت می‌شود و مانع رسیدگی نیست، ولی ادعای طرف
                دیگر را خودکار اثبات نمی‌کند.
              </p>
              <div className="mt-3">
                <Button
                  onClick={() =>
                    void act(async () => {
                      await api(`/api/disputes/${dispute.id}/adjudicate`, { method: "POST" });
                    })
                  }
                  busy={busy}
                  data-testid="adjudicate"
                >
                  درخواست صدور حکم
                </Button>
              </div>
            </Card>
          )}

          {dispute.decision && (
            <Card title="حکم داوری">
              <p className="text-sm leading-7">{String(dispute.decision.reason)}</p>
              <p className="mt-1 text-xs text-ink-500">
                وضعیت: {String(dispute.decision.status)} — اعمال‌شده:{" "}
                {dispute.decision.appliedAt
                  ? tehranTime(String(dispute.decision.appliedAt))
                  : "هنوز خیر"}
              </p>
              <ul className="mt-3 flex flex-col gap-2 text-sm">
                {(dispute.decision.lineDecisions as Array<Record<string, unknown>>).map(
                  (line, index) => (
                    <li key={index} className="rounded-lg border border-ink-200 p-2">
                      <p className="font-semibold">قلم: {String(line.claimItemId)}</p>
                      <p className="tabular text-xs">
                        نتیجه: {String(line.verdict)} — مبلغ پذیرفته‌شده:{" "}
                        {toman(Number(line.acceptedAmountToman ?? 0))}
                      </p>
                      <p className="text-xs text-ink-500">{String(line.reason ?? "")}</p>
                    </li>
                  ),
                )}
              </ul>
            </Card>
          )}

          {dispute.settlement && (
            <Card title="نتیجهٔ تسویه">
              <p className="text-sm">
                منشأ:{" "}
                <strong>
                  {dispute.settlement.source === "ai_decision"
                    ? "حکم داوری هوش مصنوعی"
                    : "توافق داوطلبانهٔ طرفین"}
                </strong>
              </p>
              <LineItems lines={dispute.settlement.lines as never} />
              <dl className="mt-3 grid gap-3 rounded-lg border border-ink-200 bg-ink-50 p-3 text-sm sm:grid-cols-2">
                <div>
                  <dt className="text-ink-500">مبلغ کل تسویه</dt>
                  <dd className="tabular text-base font-bold">
                    {toman(Number(dispute.settlement.totalToman ?? 0))}
                  </dd>
                </div>
                <div>
                  <dt className="text-ink-500">قابل پرداخت به متخصص</dt>
                  <dd className="tabular text-base font-bold">
                    {toman(Number(dispute.settlement.specialistPayableToman ?? 0))}
                  </dd>
                </div>
              </dl>
              <p className="mt-2 text-xs text-ink-500">{String(dispute.settlement.note ?? "")}</p>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
