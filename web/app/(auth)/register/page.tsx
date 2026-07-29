"use client";

import { useState, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import { Eye, EyeOff, Loader2 } from "lucide-react";
import { register, login, fetchAuthStatus, safeAuthRedirect } from "@/lib/auth";
import { TraitTutorMark } from "@/components/brand/TraitTutorMark";

export default function RegisterPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = safeAuthRedirect(searchParams.get("next"));

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [passwordVisible, setPasswordVisible] = useState(false);

  useEffect(() => {
    // Redirect if already logged in
    fetchAuthStatus().then((status) => {
      if (status?.authenticated) router.replace("/");
    });

  }, [router]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");

    if (password !== confirmPassword) {
      setError(t("Passwords do not match"));
      return;
    }

    setLoading(true);
    const result = await register(username.trim(), password);

    if (result.ok) {
      const signedIn = await login(username.trim(), password);
      if (signedIn.ok) router.replace(next);
      else router.replace(`/login?registered=1&next=${encodeURIComponent(next)}`);
    } else {
      setError(result.error ?? t("Registration failed"));
      setLoading(false);
    }
  }

  return (
    <div className="w-full max-w-sm">
      {/* Logo / Title */}
      <div className="text-center mb-8">
        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center border border-cyan-400/30 bg-cyan-400/5"><TraitTutorMark className="h-8 w-8" /></div>
        <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-cyan-600 dark:text-cyan-300">TraitTutor</p>
        <h1 className="mt-2 font-serif text-2xl font-semibold text-[var(--foreground)] tracking-tight">{t("Start learning your way")}</h1>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">
          {t("Create your account")}
        </p>
      </div>

      {/* First-user notice */}

      {/* Card */}
      <div className="border border-[var(--border)] bg-[var(--card)]/80 px-8 py-8 shadow-[0_24px_70px_rgba(0,0,0,0.12)] backdrop-blur">
        <form onSubmit={handleSubmit} className="space-y-5">
          {/* Email or username */}
          <div>
            <label
              htmlFor="username"
              className="block text-sm font-medium text-[var(--foreground)] mb-1.5"
            >
              {t("Email or username")}
            </label>
            <input
              id="username"
              type="text"
              autoComplete="username"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-3.5 py-2.5 rounded-lg border border-[var(--border)]
                         bg-[var(--background)] text-[var(--foreground)]
                         placeholder:text-[var(--muted-foreground)]
                         focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent
                         transition-shadow text-sm"
              placeholder={t("you@example.com")}
            />
          </div>

          {/* Password */}
          <div>
            <label
              htmlFor="password"
              className="block text-sm font-medium text-[var(--foreground)] mb-1.5"
            >
              {t("Password")}
            </label>
            <div className="relative"><input
              id="password"
              type={passwordVisible ? "text" : "password"}
              autoComplete="new-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3.5 py-2.5 pr-11 rounded-lg border border-[var(--border)]
                         bg-[var(--background)] text-[var(--foreground)]
                         placeholder:text-[var(--muted-foreground)]
                         focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent
                         transition-shadow text-sm"
              placeholder="••••••••"
            /><button type="button" onClick={() => setPasswordVisible((value) => !value)} className="absolute inset-y-0 right-0 flex w-10 items-center justify-center text-[var(--muted-foreground)] hover:text-[var(--foreground)]" aria-label={passwordVisible ? t("Hide password") : t("Show password")}>{passwordVisible ? <EyeOff size={16} /> : <Eye size={16} />}</button></div>
            <p className="mt-1 text-xs text-[var(--muted-foreground)]">
              {t("At least 8 characters")}
            </p>
          </div>

          {/* Confirm Password */}
          <div>
            <label
              htmlFor="confirmPassword"
              className="block text-sm font-medium text-[var(--foreground)] mb-1.5"
            >
              {t("Confirm password")}
            </label>
            <div className="relative"><input
              id="confirmPassword"
              type={passwordVisible ? "text" : "password"}
              autoComplete="new-password"
              required
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className="w-full px-3.5 py-2.5 pr-11 rounded-lg border border-[var(--border)]
                         bg-[var(--background)] text-[var(--foreground)]
                         placeholder:text-[var(--muted-foreground)]
                         focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent
                         transition-shadow text-sm"
              placeholder="••••••••"
            /><button type="button" onClick={() => setPasswordVisible((value) => !value)} className="absolute inset-y-0 right-0 flex w-10 items-center justify-center text-[var(--muted-foreground)] hover:text-[var(--foreground)]" aria-label={passwordVisible ? t("Hide password") : t("Show password")}>{passwordVisible ? <EyeOff size={16} /> : <Eye size={16} />}</button></div>
          </div>

          {/* Error message */}
          {error && (
            <p className="text-sm text-red-500 bg-red-500/10 rounded-lg px-3 py-2">
              {error}
            </p>
          )}

          {/* Submit */}
          <button
            type="submit"
            disabled={loading}
            className="w-full py-2.5 px-4 rounded-lg font-medium text-sm
                       bg-[var(--primary)] text-[var(--primary-foreground)]
                       hover:opacity-90 active:opacity-80
                       disabled:opacity-50 disabled:cursor-not-allowed
                       transition-opacity"
          >
              {loading ? <span className="inline-flex items-center gap-2"><Loader2 size={15} className="animate-spin" />{t("Creating account…")}</span> : t("Create account")}
          </button>
        </form>
      </div>

      <p className="mt-6 text-center text-sm text-[var(--muted-foreground)]">
        {t("Already have an account?")}{" "}
        <Link
          href={`/login?next=${encodeURIComponent(next)}`}
          className="text-[var(--primary)] hover:underline font-medium"
        >
          {t("Sign in")}
        </Link>
      </p>

      <p className="mt-3 text-center text-xs text-[var(--muted-foreground)]">
        {t("Your learning, in your own rhythm.")}
      </p>
    </div>
  );
}
