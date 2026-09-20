import Link from "next/link";
import { Card, InfoNote, SampleTag } from "@/components/ui";

const STEPS = [
  {
    title: "۱. شرح و تکمیل",
    body: "خودرو، نشانه‌ها و بازهٔ مراجعه را ثبت می‌کنید؛ دستیار پرسش‌های لازم را یکجا می‌پرسد و خلاصه را می‌سازد.",
  },
  {
    title: "۲. پیشنهاد و مقایسه",
    body: "همهٔ متخصصان مرتبط درخواست را می‌بینند و پیشنهاد ساختاریافته می‌دهند. قیمت آزاد است و مقایسه به تفکیک سناریو انجام می‌شود.",
  },
  {
    title: "۳. توافق و تغییر",
    body: "اقلام و مبلغ در نسخهٔ توافق ثبت می‌شوند. هر تغییر با تأیید صریح هر دو طرف روی همان نسخه فعال می‌شود.",
  },
  {
    title: "۴. رسید و پایان",
    body: "مخارج و ریز اقلام به تأیید شما می‌رسند. اختلاف فاحش قیمت قطعه با ترب به شما اطلاع داده می‌شود.",
  },
  {
    title: "۵. اختلاف و داوری",
    body: "اگر اختلاف با توافق حل نشود، حکم هوش مصنوعی دربارهٔ اقلام و مبلغ این پرونده لازم‌الاجرا ثبت و ابلاغ می‌شود.",
  },
  {
    title: "۶. ارزیابی و بازپرداخت",
    body: "افزایش بی‌دلیل قیمت در امتیاز شفافیت هزینهٔ متخصص ثبت می‌شود و بررسی بازپرداخت هزینهٔ ثبت درخواست رایگان است.",
  },
];

export default function HomePage() {
  return (
    <div className="flex flex-col gap-6">
      <section className="rounded-2xl border border-ink-200 bg-white p-5 sm:p-8">
        <h1 className="text-2xl font-bold sm:text-3xl">
          هزینهٔ تعمیر خودرو را پیش از مراجعه بفهمید
        </h1>
        <p className="mt-3 max-w-3xl leading-7 text-ink-700">
          ترب تعمیر درخواست شما را با کمک هوش مصنوعی روشن می‌کند، پیشنهاد متخصصان را
          قابل مقایسه می‌کند و هر تغییر هزینه را نسخه‌دار ثبت می‌کند. هدف، فهمیدن اقلام
          و شرایط قیمت و کم‌شدن غافلگیری هزینه است — نه تشخیص قطعی خرابی یا تضمین
          کیفیت تعمیر.
        </p>
        <div className="mt-5 flex flex-wrap gap-3">
          <Link
            href="/requests/new"
            className="inline-flex min-h-11 items-center rounded-lg bg-brand-500 px-5 py-2.5 font-semibold text-white hover:bg-brand-600"
          >
            شروع درخواست تعمیر
          </Link>
          <Link
            href="/sign-in"
            className="inline-flex min-h-11 items-center rounded-lg border border-ink-200 bg-white px-5 py-2.5 font-semibold hover:bg-ink-100"
          >
            ورود با حساب نمونه
          </Link>
        </div>
      </section>

      <Card
        title="پوشش این نمونهٔ نمایشی"
        subtitle="دامنهٔ دمو محدود است تا قواعد محصول با دادهٔ واقعی‌نما آزمون‌پذیر بماند."
      >
        <ul className="grid gap-2 text-sm leading-7 sm:grid-cols-2">
          <li>شهر: تهران — محلهٔ نمونهٔ مشتری: تهرانسر</li>
          <li>خودرو: پژو ۲۰۶ تیپ ۵</li>
          <li>محل انجام خدمت: تعمیرگاه</li>
          <li>خدمات: کیت کلاچ، لنت ترمز جلو، عیب‌یابی خنک‌کاری</li>
        </ul>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <SampleTag>دادهٔ نمونه</SampleTag>
          <SampleTag>پرداخت شبیه‌سازی‌شده</SampleTag>
          <SampleTag>پاسخ AI نمایشی</SampleTag>
        </div>
        <div className="mt-4">
          <InfoNote>
            خارج از این پوشش، پیش از هر پرداختی پیام «فعلاً در پوشش این دمو نیست» نمایش
            داده می‌شود. مبلغ ثبت درخواست در این نمونه ۲٬۰۰۰ تومان و آزمایشی است.
          </InfoNote>
        </div>
      </Card>

      <Card title="مسیر کامل کار">
        <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {STEPS.map((step) => (
            <li key={step.title} className="rounded-lg border border-ink-200 bg-ink-50 p-3">
              <h3 className="text-sm font-bold">{step.title}</h3>
              <p className="mt-1 text-sm leading-6 text-ink-700">{step.body}</p>
            </li>
          ))}
        </ol>
      </Card>
    </div>
  );
}
