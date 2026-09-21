"use client";

import { useSession } from "@/components/session-context";
import { toman, type LineItem } from "@/lib/api";

const TYPE_LABELS: Record<string, string> = {
  part: "قطعه",
  labor: "اجرت",
  extra: "سایر",
};

/** Work time in the words a customer uses, rather than a raw minute count. */
function duration(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) return `${rest.toLocaleString("fa-IR")} دقیقه`;
  if (rest === 0) return `${hours.toLocaleString("fa-IR")} ساعت`;
  return `${hours.toLocaleString("fa-IR")} ساعت و ${rest.toLocaleString("fa-IR")} دقیقه`;
}

/**
 * A customer asks who brings the part and where their money goes; which side the payout
 * lands on is the specialist's and support's question, not theirs.
 */
function settlement(line: LineItem, forCustomer: boolean): string {
  if (!forCustomer) return line.paidTo === "specialist" ? "متخصص" : "فروشندهٔ ثالث";
  if (line.suppliedBy === "customer") return "خودتان تهیه می‌کنید";
  if (line.paidTo === "third_party") return "پرداخت مستقیم به فروشنده";
  return "در صورتحساب متخصص";
}

/**
 * On desktop this is a comparison table; on a narrow screen each row becomes a card, so
 * nothing needs horizontal scrolling. An unknown amount is shown as "نامعلوم", never as
 * zero, and a line settled with a third party is marked so the two totals stay legible.
 */
export function LineItems({ lines }: { lines: LineItem[] }) {
  const { user } = useSession();
  const forCustomer = user?.role === "customer";

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
            <th className="py-2 font-medium">{forCustomer ? "زمان / تعداد" : "تعداد / زمان"}</th>
            <th className="py-2 font-medium">{forCustomer ? "تهیه و پرداخت" : "پرداخت به"}</th>
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
                  ? forCustomer
                    ? duration(line.minutes)
                    : `${line.minutes.toLocaleString("fa-IR")} دقیقه`
                  : line.quantity !== null && line.quantity !== undefined
                    ? line.quantity.toLocaleString("fa-IR")
                    : "—"}
              </td>
              <td className="py-2">{settlement(line, forCustomer)}</td>
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
              {line.minutes
                ? ` — ${forCustomer ? duration(line.minutes) : `${line.minutes.toLocaleString("fa-IR")} دقیقه`}`
                : ""}
              {line.quantity ? ` — تعداد ${line.quantity.toLocaleString("fa-IR")}` : ""}
              {forCustomer ? " — " : " — پرداخت به "}
              {settlement(line, forCustomer)}
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
  const { user } = useSession();
  const forCustomer = user?.role === "customer";
  const forSpecialist = user?.role === "specialist";

  return (
    <dl className="mt-3 grid gap-3 rounded-lg border border-ink-200 bg-ink-50 p-3 text-sm sm:grid-cols-2">
      <div>
        <dt className="text-ink-500">مبلغ کل خدمت</dt>
        <dd className="tabular text-base font-bold">{toman(totalToman)}</dd>
      </div>
      <div>
        <dt className="text-ink-500">
          {forSpecialist ? "سهم شما" : forCustomer ? "پرداختی شما به متخصص" : "قابل پرداخت به متخصص"}
        </dt>
        <dd className="tabular text-base font-bold">{toman(specialistPayableToman)}</dd>
      </div>
      <p className="text-xs text-ink-500 sm:col-span-2">
        {forSpecialist
          ? "قطعه‌ای که مشتری خودش می‌خرد در مبلغ کل هست ولی به حساب شما اضافه نمی‌شود."
          : "قطعه‌ای که خودتان می‌خرید در مبلغ کل هست ولی دوباره به حساب متخصص اضافه نمی‌شود."}
      </p>
    </dl>
  );
}
