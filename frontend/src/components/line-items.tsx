"use client";

import { toman, type LineItem } from "@/lib/api";

const TYPE_LABELS: Record<string, string> = {
  part: "قطعه",
  labor: "اجرت",
  extra: "سایر",
};

/**
 * On desktop this is a comparison table; on a narrow screen each row becomes a card, so
 * nothing needs horizontal scrolling. An unknown amount is shown as "نامعلوم", never as
 * zero, and a line settled with a third party is marked so the two totals stay legible.
 */
export function LineItems({ lines }: { lines: LineItem[] }) {
  if (lines.length === 0) {
    return <p className="text-sm text-ink-500">قلمی ثبت نشده است.</p>;
  }

  return (
    <>
      <table className="hidden w-full text-sm sm:table">
        <thead>
          <tr className="border-b border-ink-200 text-right text-xs text-ink-500">
            <th className="py-2 font-medium">نوع</th>
            <th className="py-2 font-medium">عنوان</th>
            <th className="py-2 font-medium">تعداد / زمان</th>
            <th className="py-2 font-medium">پرداخت به</th>
            <th className="py-2 font-medium">مبلغ</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => (
            <tr key={line.id} className="border-b border-ink-100 last:border-0">
              <td className="py-2">{TYPE_LABELS[line.type] ?? line.type}</td>
              <td className="py-2">
                {line.title}
                {line.spec && <span className="block text-xs text-ink-500">{line.spec}</span>}
              </td>
              <td className="tabular py-2">
                {line.minutes !== null && line.minutes !== undefined
                  ? `${line.minutes.toLocaleString("fa-IR")} دقیقه`
                  : line.quantity !== null && line.quantity !== undefined
                    ? line.quantity.toLocaleString("fa-IR")
                    : "—"}
              </td>
              <td className="py-2">
                {line.paidTo === "specialist" ? "متخصص" : "فروشندهٔ ثالث"}
              </td>
              <td className="tabular py-2 font-semibold">{toman(line.amountToman)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <ul className="flex flex-col gap-2 sm:hidden">
        {lines.map((line) => (
          <li key={line.id} className="rounded-lg border border-ink-200 p-3 text-sm">
            <div className="flex items-start justify-between gap-2">
              <span className="font-semibold">{line.title}</span>
              <span className="tabular font-bold">{toman(line.amountToman)}</span>
            </div>
            <p className="mt-1 text-xs text-ink-500">
              {TYPE_LABELS[line.type] ?? line.type}
              {line.minutes ? ` — ${line.minutes.toLocaleString("fa-IR")} دقیقه` : ""}
              {line.quantity ? ` — تعداد ${line.quantity.toLocaleString("fa-IR")}` : ""}
              {" — پرداخت به "}
              {line.paidTo === "specialist" ? "متخصص" : "فروشندهٔ ثالث"}
            </p>
          </li>
        ))}
      </ul>
    </>
  );
}

export function Totals({
  totalToman,
  specialistPayableToman,
}: {
  totalToman: number | null | undefined;
  specialistPayableToman: number | null | undefined;
}) {
  return (
    <dl className="mt-3 grid gap-3 rounded-lg border border-ink-200 bg-ink-50 p-3 text-sm sm:grid-cols-2">
      <div>
        <dt className="text-ink-500">مبلغ کل خدمت</dt>
        <dd className="tabular text-base font-bold">{toman(totalToman)}</dd>
      </div>
      <div>
        <dt className="text-ink-500">قابل پرداخت به متخصص</dt>
        <dd className="tabular text-base font-bold">{toman(specialistPayableToman)}</dd>
      </div>
      <p className="text-xs text-ink-500 sm:col-span-2">
        قطعه‌ای که خودتان می‌خرید در مبلغ کل هست ولی دوباره به حساب متخصص اضافه نمی‌شود.
      </p>
    </dl>
  );
}
