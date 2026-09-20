"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, tehranTime, type CurrentUser } from "@/lib/api";

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

export function SessionBar() {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [ready, setReady] = useState(false);
  const router = useRouter();
  const pathname = usePathname();

  const load = useCallback(async () => {
    try {
      setUser(await api<CurrentUser | null>("/api/auth/me"));
    } catch {
      setUser(null);
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, pathname]);

  const signOut = async () => {
    await api<void>("/api/auth/sign-out", { method: "POST" });
    setUser(null);
    router.push("/");
    router.refresh();
  };

  if (!ready) return <span className="text-xs text-ink-500">…</span>;

  if (!user) {
    return (
      <Link
        href="/sign-in"
        className="rounded-lg bg-brand-500 px-3 py-2 text-sm font-semibold text-white hover:bg-brand-600"
      >
        ورود با حساب نمونه
      </Link>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <Link href={ROLE_HOME[user.role] ?? "/"} className="font-medium hover:text-brand-600">
        {user.displayName}
      </Link>
      <span className="rounded-full border border-ink-200 bg-ink-100 px-2 py-0.5 text-xs text-ink-700">
        {ROLE_LABELS[user.role] ?? user.role}
      </span>
      {user.clockOffsetSeconds > 0 && (
        <span
          className="rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-xs text-amber-900"
          title={`ساعت دمو جلو برده شده است: ${tehranTime(user.serverTime)}`}
        >
          ساعت دمو جلو رفته
        </span>
      )}
      <button
        type="button"
        onClick={() => void signOut()}
        className="min-h-9 rounded-lg border border-ink-200 px-3 py-1.5 text-xs hover:bg-ink-100"
      >
        خروج
      </button>
    </div>
  );
}
