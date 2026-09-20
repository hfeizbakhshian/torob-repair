import type { Metadata, Viewport } from "next";
import Link from "next/link";
import "./globals.css";
import { SessionBar } from "@/components/session-bar";

export const metadata: Metadata = {
  title: "ترب تعمیر — مقایسهٔ خدمات تعمیر خودرو",
  description:
    "نمونهٔ نمایشی مقایسهٔ خدمات تعمیر خودرو با دستیار هوشمند: روشن‌کردن درخواست، "
    + "مقایسهٔ پیشنهادها، ثبت توافق و رسید، داوری اختلاف و بازپرداخت.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fa" dir="rtl">
      <body className="min-h-screen">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:m-2 focus:rounded-md focus:bg-white focus:px-3 focus:py-2 focus:shadow"
        >
          پرش به محتوای اصلی
        </a>

        <div className="border-b border-ink-200 bg-brand-500 text-white">
          <p className="mx-auto max-w-6xl px-4 py-1.5 text-center text-xs sm:text-sm">
            نسخهٔ نمایشی — همهٔ داده‌ها، قیمت‌ها، پرداخت‌ها و ارزیابی‌ها نمونه‌اند و
            انتقال وجه واقعی انجام نمی‌شود.
          </p>
        </div>

        <header className="border-b border-ink-200 bg-white">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
            <Link href="/" className="flex items-baseline gap-2">
              <span className="text-lg font-bold text-brand-600">ترب تعمیر</span>
              <span className="text-xs text-ink-500">مقایسهٔ خدمات تعمیر خودرو</span>
            </Link>
            <SessionBar />
          </div>
        </header>

        <main id="main" className="mx-auto max-w-6xl px-4 py-6">
          {children}
        </main>

        <footer className="mt-10 border-t border-ink-200 bg-white">
          <div className="mx-auto max-w-6xl px-4 py-5 text-xs leading-6 text-ink-500">
            <p>
              این پروژه یک نمونهٔ مستقل است و وابستگی رسمی به ترب ندارد. لینک‌های قطعه
              فقط جست‌وجوی ترب را باز می‌کنند و اطلاعات قیمت دستی ثبت شده‌اند؛ موجودی
              زنده یا قیمت تأییدشده ادعا نمی‌شود.
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
