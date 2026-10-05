"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

type ToastType = "error" | "warning" | "success" | "info";

type Toast = {
  id: string;
  type: ToastType;
  message: string;
  persistent?: boolean;
};

type ToastContextValue = {
  addToast: (type: ToastType, message: string, opts?: { persistent?: boolean }) => string;
  removeToast: (id: string) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}

let nextId = 0;

const DURATION_MS: Record<ToastType, number> = {
  error: 8000,
  warning: 6000,
  success: 4000,
  info: 5000,
};

const TYPE_STYLES: Record<ToastType, { bg: string; border: string; icon: string }> = {
  error: {
    bg: "hsl(var(--state-critical) / 0.1)",
    border: "hsl(var(--state-critical) / 0.35)",
    icon: "hsl(var(--state-critical))",
  },
  warning: {
    bg: "hsl(var(--state-warning) / 0.1)",
    border: "hsl(var(--state-warning) / 0.35)",
    icon: "hsl(var(--state-warning))",
  },
  success: {
    bg: "hsl(var(--state-success) / 0.1)",
    border: "hsl(var(--state-success) / 0.35)",
    icon: "hsl(var(--state-success))",
  },
  info: {
    bg: "hsl(var(--state-info) / 0.1)",
    border: "hsl(var(--state-info) / 0.35)",
    icon: "hsl(var(--state-info))",
  },
};

function ToastIcon({ type }: { type: ToastType }) {
  const color = TYPE_STYLES[type].icon;
  const cls = "h-4 w-4 shrink-0";

  if (type === "error") {
    return (
      <svg viewBox="0 0 24 24" className={cls} fill="none" stroke={color} strokeWidth="2">
        <circle cx="12" cy="12" r="10" />
        <path d="M15 9l-6 6M9 9l6 6" />
      </svg>
    );
  }
  if (type === "warning") {
    return (
      <svg viewBox="0 0 24 24" className={cls} fill="none" stroke={color} strokeWidth="2">
        <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
        <path d="M12 9v4M12 17h.01" />
      </svg>
    );
  }
  if (type === "success") {
    return (
      <svg viewBox="0 0 24 24" className={cls} fill="none" stroke={color} strokeWidth="2">
        <circle cx="12" cy="12" r="10" />
        <path d="M9 12l2 2 4-4" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" className={cls} fill="none" stroke={color} strokeWidth="2">
      <circle cx="12" cy="12" r="10" />
      <path d="M12 16v-4M12 8h.01" />
    </svg>
  );
}

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  const style = TYPE_STYLES[toast.type];

  useEffect(() => {
    if (toast.persistent) return;
    const timer = setTimeout(onDismiss, DURATION_MS[toast.type]);
    return () => clearTimeout(timer);
  }, [toast, onDismiss]);

  return (
    <div
      role="alert"
      className="flex items-start gap-2 rounded-md px-3 py-2.5 text-sm shadow-md animate-in slide-in-from-right"
      style={{
        background: style.bg,
        border: `1px solid ${style.border}`,
        color: "hsl(var(--foreground))",
        backdropFilter: "blur(8px)",
        maxWidth: 400,
      }}
    >
      <ToastIcon type={toast.type} />
      <span className="flex-1 leading-snug">{toast.message}</span>
      <button
        onClick={onDismiss}
        className="shrink-0 rounded p-0.5 opacity-60 hover:opacity-100"
        style={{ color: "hsl(var(--foreground))" }}
        aria-label="Dismiss"
      >
        <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M18 6L6 18M6 6l12 12" />
        </svg>
      </button>
    </div>
  );
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const addToast = useCallback((type: ToastType, message: string, opts?: { persistent?: boolean }) => {
    const id = `toast-${++nextId}`;
    setToasts((prev) => [...prev.slice(-4), { id, type, message, persistent: opts?.persistent }]);
    return id;
  }, []);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return (
    <ToastContext.Provider value={{ addToast, removeToast }}>
      {children}
      {mounted &&
        createPortal(
          <div
            className="fixed right-4 top-4 z-[9999] flex flex-col gap-2"
            aria-live="polite"
            aria-label="Notifications"
          >
            {toasts.map((t) => (
              <ToastItem key={t.id} toast={t} onDismiss={() => removeToast(t.id)} />
            ))}
          </div>,
          document.body,
        )}
    </ToastContext.Provider>
  );
}
