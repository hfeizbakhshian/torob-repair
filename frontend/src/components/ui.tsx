"use client";

import type { ReactNode } from "react";

export function Card({
  title,
  subtitle,
  children,
  actions,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-ink-200 bg-white p-4 shadow-sm sm:p-5">
      {(title || actions) && (
        <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
          <div>
            {title && <h2 className="text-base font-bold sm:text-lg">{title}</h2>}
            {subtitle && <p className="mt-1 text-sm text-ink-500">{subtitle}</p>}
          </div>
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

const BUTTON_STYLES = {
  primary: "bg-brand-500 text-white hover:bg-brand-600 disabled:bg-ink-200 disabled:text-ink-500",
  secondary:
    "border border-ink-200 bg-white text-ink-900 hover:bg-ink-100 disabled:text-ink-500",
  danger: "border border-brand-500 bg-white text-brand-700 hover:bg-brand-50",
} as const;

export function Button({
  children,
  onClick,
  type = "button",
  variant = "primary",
  disabled,
  busy,
  ...rest
}: {
  children: ReactNode;
  onClick?: () => void;
  type?: "button" | "submit";
  variant?: keyof typeof BUTTON_STYLES;
  disabled?: boolean;
  busy?: boolean;
} & { "data-testid"?: string }) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={`inline-flex min-h-11 items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition ${BUTTON_STYLES[variant]} disabled:cursor-not-allowed`}
      {...rest}
    >
      {busy ? "در حال انجام…" : children}
    </button>
  );
}

/** An error is announced with text, not colour alone. */
export function ErrorNote({ message, fields }: { message: string; fields?: Record<string, string> | null }) {
  return (
    <div role="alert" className="rounded-lg border border-brand-500 bg-brand-50 p-3 text-sm">
      <p className="font-semibold text-brand-700">⚠ {message}</p>
      {fields && Object.keys(fields).length > 0 && (
        <ul className="mt-2 list-inside list-disc text-brand-700">
          {Object.entries(fields).map(([field, text]) => (
            <li key={field}>
              <span className="font-medium">{field}:</span> {text}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function InfoNote({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "warn" }) {
  const styles =
    tone === "warn"
      ? "border-amber-400 bg-amber-50 text-amber-900"
      : "border-ink-200 bg-ink-50 text-ink-700";
  return <p className={`rounded-lg border p-3 text-sm leading-6 ${styles}`}>{children}</p>;
}

export function SampleTag({ children = "نمونه" }: { children?: ReactNode }) {
  return (
    <span className="rounded-full border border-ink-200 bg-ink-100 px-2 py-0.5 text-[11px] font-medium text-ink-700">
      {children}
    </span>
  );
}

export function StatusPill({ label, tone = "neutral" }: { label: string; tone?: "neutral" | "good" | "warn" | "bad" }) {
  const styles = {
    neutral: "border-ink-200 bg-ink-100 text-ink-700",
    good: "border-emerald-300 bg-emerald-50 text-emerald-800",
    warn: "border-amber-300 bg-amber-50 text-amber-900",
    bad: "border-brand-500 bg-brand-50 text-brand-700",
  }[tone];
  return (
    <span className={`inline-block rounded-full border px-2.5 py-1 text-xs font-medium ${styles}`}>
      {label}
    </span>
  );
}

export function Field({
  label,
  hint,
  error,
  children,
  htmlFor,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
  htmlFor: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={htmlFor} className="text-sm font-medium">
        {label}
      </label>
      {hint && <p className="text-xs text-ink-500">{hint}</p>}
      {children}
      {error && (
        <p className="field-error" role="alert">
          ⚠ {error}
        </p>
      )}
    </div>
  );
}

export const inputClass =
  "min-h-11 w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm focus:border-brand-500";

export function Spinner({ label = "در حال بارگذاری…" }: { label?: string }) {
  return (
    <p className="py-6 text-center text-sm text-ink-500" role="status">
      {label}
    </p>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-6 text-center text-sm text-ink-500">{children}</p>;
}
