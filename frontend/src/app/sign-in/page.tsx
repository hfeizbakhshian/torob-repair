"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError, type DemoAccount } from "@/lib/api";
import { Card, ErrorNote, InfoNote, Spinner } from "@/components/ui";

const ROLE_LABELS: Record<string, string> = {
  customer: "مشتری",
  specialist: "متخصص",
  support: "پشتیبانی",
};

const ROLE_HOME: Record<string, string> = {
  customer: "/requests",
  specialist: "/specialist",
  support: "/support",
};

export default function SignInPage() {
  const [accounts, setAccounts] = useState<DemoAccount[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const router = useRouter();

  useEffect(() => {
    api<DemoAccount[]>("/api/auth/demo-accounts")
      .then(setAccounts)
      .catch((problem: ApiError) => setError(problem));
  }, []);

  const signIn = async (account: DemoAccount) => {
    setBusy(true);
    setError(null);
    try {
      await api("/api/auth/sign-in", { method: "POST", body: { loginKey: account.loginKey } });
      router.push(ROLE_HOME[account.role] ?? "/");
      router.refresh();
    } catch (problem) {
      setError(problem as ApiError);
    } finally {
      setBusy(false);
    }
  };

  const grouped = (accounts ?? []).reduce<Record<string, DemoAccount[]>>((all, account) => {
    (all[account.role] ??= []).push(account);
    return all;
  }, {});

  return (
    <div className="flex max-w-3xl flex-col gap-4">
      <Card
        title="ورود با حساب نمونه"
        subtitle="این نصب نمایشی است: ثبت‌نام عمومی، پیامک و شمارهٔ تلفن واقعی وجود ندارد."
      >
        <InfoNote>
          نشست و کنترل دسترسی واقعی و سمت سرور است. با تعویض حساب نقش شما هم عوض می‌شود؛
          هیچ فیلدی در فرم نمی‌تواند نقش تازه‌ای به شما بدهد.
        </InfoNote>

        {error && (
          <div className="mt-4">
            <ErrorNote message={error.message} fields={error.fieldErrors} />
          </div>
        )}

        {accounts === null && !error && <Spinner label="در حال دریافت حساب‌های نمونه…" />}

        <div className="mt-4 flex flex-col gap-5">
          {Object.entries(grouped).map(([role, list]) => (
            <div key={role}>
              <h3 className="mb-2 text-sm font-bold">{ROLE_LABELS[role] ?? role}</h3>
              <ul className="grid gap-2 sm:grid-cols-2">
                {list.map((account) => (
                  <li key={account.loginKey}>
                    <button
                      type="button"
                      data-testid={`sign-in-${account.loginKey}`}
                      onClick={() => void signIn(account)}
                      disabled={busy}
                      className="flex min-h-14 w-full flex-col items-start rounded-lg border border-ink-200 bg-white px-3 py-2 text-right hover:border-brand-500 hover:bg-brand-50 disabled:opacity-60"
                    >
                      <span className="text-sm font-semibold">{account.displayName}</span>
                      <span className="text-xs text-ink-500">
                        {account.shopName
                          ? `${account.shopName} — ${account.city}`
                          : account.loginKey}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
