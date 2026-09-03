"use client";

import { useEffect, useId, useRef } from "react";

type TurnstileApi = {
  render: (
    container: string | HTMLElement,
    options: {
      sitekey: string;
      callback: (token: string) => void;
      "error-callback"?: () => void;
      "expired-callback"?: () => void;
      "timeout-callback"?: () => void;
    },
  ) => string;
  reset: (widgetId?: string) => void;
  remove: (widgetId?: string) => void;
};

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

type Props = {
  siteKey: string;
  onToken: (token: string | null) => void;
  onStatus: (status: "loading" | "ready" | "error" | "expired") => void;
  resetSignal: number;
};

let scriptPromise: Promise<void> | null = null;

function loadTurnstileScript(): Promise<void> {
  if (window.turnstile) {
    return Promise.resolve();
  }
  if (scriptPromise) {
    return scriptPromise;
  }
  scriptPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(
      "script[data-opspilot-turnstile]",
    );
    if (existing) {
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener(
        "error",
        () => reject(new Error("turnstile_script")),
        { once: true },
      );
      return;
    }
    const script = document.createElement("script");
    script.src =
      "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
    script.async = true;
    script.dataset.opspilotTurnstile = "true";
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("turnstile_script"));
    document.head.appendChild(script);
  });
  return scriptPromise;
}

export function TurnstileWidget({
  siteKey,
  onToken,
  onStatus,
  resetSignal,
}: Props) {
  const containerId = useId().replace(/:/g, "");
  const widgetId = useRef<string | null>(null);
  const onTokenRef = useRef(onToken);
  const onStatusRef = useRef(onStatus);

  useEffect(() => {
    onTokenRef.current = onToken;
    onStatusRef.current = onStatus;
  }, [onToken, onStatus]);

  useEffect(() => {
    let cancelled = false;
    onStatusRef.current("loading");
    onTokenRef.current(null);

    void loadTurnstileScript()
      .then(() => {
        if (cancelled || !window.turnstile) {
          return;
        }
        const host = document.getElementById(containerId);
        if (!host) {
          return;
        }
        host.replaceChildren();
        widgetId.current = window.turnstile.render(host, {
          sitekey: siteKey,
          callback: (token) => {
            onTokenRef.current(token);
            onStatusRef.current("ready");
          },
          "error-callback": () => {
            onTokenRef.current(null);
            onStatusRef.current("error");
          },
          "expired-callback": () => {
            onTokenRef.current(null);
            onStatusRef.current("expired");
          },
          "timeout-callback": () => {
            onTokenRef.current(null);
            onStatusRef.current("error");
          },
        });
      })
      .catch(() => {
        if (!cancelled) {
          onStatusRef.current("error");
          onTokenRef.current(null);
        }
      });

    return () => {
      cancelled = true;
      if (widgetId.current && window.turnstile) {
        try {
          window.turnstile.remove(widgetId.current);
        } catch {
          // Widget may already be gone during unmount.
        }
      }
      widgetId.current = null;
    };
  }, [containerId, siteKey, resetSignal]);

  return (
    <div className="turnstile-wrap">
      <div id={containerId} className="turnstile-host" />
    </div>
  );
}
