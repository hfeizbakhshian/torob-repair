"use client";

import type { Schemas } from "@/lib/api";

type Specialist = Schemas["SpecialistPublicOut"];

/**
 * The cost-transparency score is never shown as a bare number: the denominator, the
 * window and the breakdown of causes go with it, and below the minimum history no number
 * is shown at all.
 */
export function ScoreBadge({ specialist }: { specialist: Specialist }) {
  return (
    <div className="rounded-lg border border-ink-200 bg-ink-50 p-3 text-xs leading-6">
      <p className="font-semibold">
        شفافیت هزینه:{" "}
        {specialist.hasEnoughHistory && specialist.score != null ? (
          <span className="tabular text-sm">{specialist.score.toLocaleString("fa-IR")} از ۱۰۰</span>
        ) : (
          <span className="text-ink-700">سابقهٔ کافی نیست</span>
        )}
      </p>
      <p className="text-ink-700">
        پروندهٔ قابل ارزیابی: {specialist.assessableCases.toLocaleString("fa-IR")} — پروندهٔ
        دارای علت منفی: {specialist.negativeCases.toLocaleString("fa-IR")}
      </p>
      <p className="text-ink-700">
        افزایش ناموجه: {specialist.unjustifiedIncreaseCases.toLocaleString("fa-IR")} — اختلاف
        فاحش قیمت فاکتور: {specialist.invoiceOverpriceCases.toLocaleString("fa-IR")}
      </p>
      <p className="text-ink-700">
        رضایت مشتری:{" "}
        {specialist.satisfactionResponses > 0 && specialist.satisfactionAverage != null
          ? `${specialist.satisfactionAverage.toLocaleString("fa-IR")} از ۵ (${specialist.satisfactionResponses.toLocaleString("fa-IR")} پاسخ)`
          : "داده‌ای نیست"}
      </p>
      <p className="mt-1 text-ink-500">
        این عدد دربارهٔ شفافیت هزینه است و کیفیت فنی تعمیر از آن نتیجه نمی‌شود.
      </p>
    </div>
  );
}
