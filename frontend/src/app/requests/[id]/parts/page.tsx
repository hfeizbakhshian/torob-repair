"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  api,
  ApiError,
  tehranTime,
  toman,
  type PartOptionOut,
  type PartSearchOut,
  type RequestOut,
} from "@/lib/api";
import {
  Button,
  Card,
  Empty,
  ErrorNote,
  Field,
  InfoNote,
  SampleTag,
  Spinner,
  inputClass,
} from "@/components/ui";

type PartSearch = PartSearchOut;

export default function PartsPage() {
  const { id: requestId } = useParams<{ id: string }>();
  const [request, setRequest] = useState<RequestOut | null>(null);
  const [options, setOptions] = useState<PartOptionOut[]>([]);
  const [search, setSearch] = useState<PartSearch | null>(null);
  const [query, setQuery] = useState("کیت کلاچ پژو ۲۰۶ تیپ ۵");
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  const [form, setForm] = useState({
    partTitle: "",
    partNumber: "",
    brand: "",
    productUrl: "",
    sellerName: "",
    priceToman: "",
    deliveryNote: "",
  });

  const load = useCallback(async () => {
    try {
      setRequest(await api<RequestOut>(`/api/requests/${requestId}`));
      setOptions(await api<PartOptionOut[]>(`/api/requests/${requestId}/parts`));
    } catch (problem) {
      setError(problem as ApiError);
    }
  }, [requestId]);

  useEffect(() => {
    void load();
  }, [load]);

  const runSearch = async () => {
    setError(null);
    try {
      setSearch(
        await api<PartSearch>(`/api/parts/search?query=${encodeURIComponent(query)}`),
      );
    } catch (problem) {
      setError(problem as ApiError);
    }
  };

  const record = async () => {
    setBusy(true);
    setError(null);
    try {
      const price = Number(form.priceToman);
      await api(`/api/requests/${requestId}/parts`, {
        method: "POST",
        body: {
          partTitle: form.partTitle,
          partNumber: form.partNumber || null,
          brand: form.brand || null,
          condition: "new",
          productUrl: form.productUrl,
          sellerName: form.sellerName || null,
          priceToman: Number.isFinite(price) && form.priceToman ? price : null,
          deliveryNote: form.deliveryNote || null,
        },
      });
      setForm({
        partTitle: "",
        partNumber: "",
        brand: "",
        productUrl: "",
        sellerName: "",
        priceToman: "",
        deliveryNote: "",
      });
      await load();
    } catch (problem) {
      setError(problem as ApiError);
    } finally {
      setBusy(false);
    }
  };

  if (!request) {
    return error ? <ErrorNote message={error.message} fields={error.fieldErrors} /> : <Spinner />;
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-bold sm:text-2xl">قطعه و لینک ترب</h1>
        <Link href={`/requests/${requestId}`} className="text-sm text-brand-600 underline">
          بازگشت به پرونده
        </Link>
      </div>

      {error && <ErrorNote message={error.message} fields={error.fieldErrors} />}

      <InfoNote>
        سرور لینک ترب را واکشی نمی‌کند و موجودی زنده یا نزدیک‌ترین فروشنده را ادعا
        نمی‌کند. جست‌وجو در ترب باز می‌شود و شما اطلاعات محصول را دستی ثبت می‌کنید.
        انتخاب قطعه نیازمند تأیید مشخصات توسط متخصص است و خرید با اقدام صریح خود شما
        نزد فروشنده انجام می‌شود.
      </InfoNote>

      <Card title="جست‌وجو در ترب">
        <div className="flex flex-col gap-3">
          <Field label="عبارت جست‌وجو" htmlFor="query">
            <input
              id="query"
              className={inputClass}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </Field>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => void runSearch()}>
              ساخت لینک جست‌وجو
            </Button>
            {search && (
              <a
                href={search.searchUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex min-h-11 items-center rounded-lg bg-brand-500 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-600"
              >
                بازکردن ترب در تب تازه
              </a>
            )}
          </div>
          {search && <p className="text-xs text-ink-500">{search.note}</p>}
        </div>
      </Card>

      <Card
        title="ثبت دستی اطلاعات محصول"
        subtitle="فقط لینک HTTPS با میزبان دقیق torob.com یا www.torob.com پذیرفته می‌شود."
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="عنوان قطعه" htmlFor="part-title">
            <input
              id="part-title"
              className={inputClass}
              value={form.partTitle}
              onChange={(event) => setForm({ ...form, partTitle: event.target.value })}
            />
          </Field>
          <Field label="کد قطعه" htmlFor="part-number">
            <input
              id="part-number"
              className={inputClass}
              value={form.partNumber}
              onChange={(event) => setForm({ ...form, partNumber: event.target.value })}
            />
          </Field>
          <Field label="برند" htmlFor="brand">
            <input
              id="brand"
              className={inputClass}
              value={form.brand}
              onChange={(event) => setForm({ ...form, brand: event.target.value })}
            />
          </Field>
          <Field label="فروشنده" htmlFor="seller">
            <input
              id="seller"
              className={inputClass}
              value={form.sellerName}
              onChange={(event) => setForm({ ...form, sellerName: event.target.value })}
            />
          </Field>
          <Field label="قیمت (تومان)" htmlFor="price">
            <input
              id="price"
              type="number"
              min={0}
              className={inputClass}
              value={form.priceToman}
              onChange={(event) => setForm({ ...form, priceToman: event.target.value })}
            />
          </Field>
          <Field label="شرایط تحویل" htmlFor="delivery">
            <input
              id="delivery"
              className={inputClass}
              value={form.deliveryNote}
              onChange={(event) => setForm({ ...form, deliveryNote: event.target.value })}
            />
          </Field>
          <div className="sm:col-span-2">
            <Field
              label="لینک محصول در ترب"
              htmlFor="product-url"
              error={error?.fieldErrors?.productUrl}
            >
              <input
                id="product-url"
                className={inputClass}
                dir="ltr"
                placeholder="https://torob.com/p/..."
                value={form.productUrl}
                onChange={(event) => setForm({ ...form, productUrl: event.target.value })}
              />
            </Field>
          </div>
        </div>
        <div className="mt-4">
          <Button
            onClick={() => void record()}
            busy={busy}
            disabled={!form.partTitle || !form.productUrl}
            data-testid="record-part"
          >
            ثبت این گزینه
          </Button>
        </div>
      </Card>

      <Card title="گزینه‌های ثبت‌شده">
        {options.length === 0 ? (
          <Empty>هنوز گزینه‌ای ثبت نشده است.</Empty>
        ) : (
          <ul className="flex flex-col gap-3">
            {options.map((option) => (
              <li key={option.id} className="rounded-lg border border-ink-200 p-3 text-sm">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <p className="font-bold">{option.partTitle}</p>
                    <p className="text-xs text-ink-500">
                      {option.brand ?? "بدون برند"} — {option.sellerName ?? "فروشندهٔ نامعلوم"}
                      {option.partNumber ? ` — کد ${option.partNumber}` : ""}
                    </p>
                  </div>
                  <span className="tabular font-bold">{toman(option.priceToman)}</span>
                </div>
                <p className="mt-2 text-xs text-ink-500">
                  زمان مشاهده: {tehranTime(option.observedAt)} — منشأ: {option.sourceLabel}
                </p>
                <p className="text-xs">
                  سازگاری:{" "}
                  {option.compatibilityConfirmedAt
                    ? `تأیید متخصص در ${tehranTime(option.compatibilityConfirmedAt)}`
                    : "هنوز به تأیید متخصص نرسیده است"}
                </p>
                <a
                  href={option.productUrl}
                  dir="ltr"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-1 inline-block break-all text-xs text-brand-600 underline"
                >
                  {option.productUrl}
                </a>
                <div className="mt-2">
                  <SampleTag>{option.sourceLabel}</SampleTag>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
