"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { KeyRound, LogOut, Trash2 } from "lucide-react";
import { fetchAuthStatus, logout } from "@/lib/auth";
import { changeAccountPassword, deleteAccount, getProfile, type ProfileInfo } from "@/lib/profile-api";
import { SettingsPageHeader } from "@/components/settings/shared";
import { useTranslation } from "react-i18next";

/** The account surface lives inside Settings so it is not a second profile app. */
export default function AccountPrivacyPage() {
  const router = useRouter();
  const { i18n } = useTranslation();
  const zh = i18n.language.toLowerCase().startsWith("zh");
  const tr = useCallback((cn: string, en: string) => zh ? cn : en, [zh]);
  const [profile, setProfile] = useState<ProfileInfo | null>(null);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [deletePassword, setDeletePassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    void fetchAuthStatus().then(async (status) => {
      if (!status?.enabled || !status.authenticated) { router.replace("/login"); return; }
      try { setProfile(await getProfile()); } catch { setError(tr("无法读取账户信息", "Unable to load account information")); }
    });
  }, [router, tr]);

  async function signOut() { await logout(); router.replace("/login"); }
  async function updatePassword(event: React.FormEvent) { event.preventDefault(); setBusy(true); setError(""); setMessage(""); try { await changeAccountPassword(currentPassword, newPassword); setCurrentPassword(""); setNewPassword(""); setMessage(tr("密码已更新", "Password updated")); } catch (cause) { setError(cause instanceof Error ? cause.message : tr("修改密码失败", "Unable to change password")); } finally { setBusy(false); } }
  async function removeAccount(event: React.FormEvent) { event.preventDefault(); setBusy(true); setError(""); try { await deleteAccount(deletePassword, confirmation); router.replace("/login"); } catch (cause) { setError(cause instanceof Error ? cause.message : tr("注销账户失败", "Unable to delete account")); setBusy(false); } }

  return <div className="mx-auto max-w-xl"><SettingsPageHeader title={tr("账户与隐私", "Account & privacy")} description={tr("管理登录凭据和你在 TraitTutor 中保留的数据。", "Manage your sign-in credentials and the data you keep in TraitTutor.")} />
    {error ? <p role="alert" className="mt-4 rounded-md bg-red-500/10 px-3 py-2 text-sm text-red-500">{error}</p> : null}{message ? <p role="status" className="mt-4 rounded-md bg-emerald-500/10 px-3 py-2 text-sm text-emerald-600">{message}</p> : null}
    <section className="mt-5 rounded-lg border border-[var(--border)] bg-[var(--card)] p-5"><p className="text-xs text-[var(--muted-foreground)]">{tr("登录邮箱", "Sign-in email")}</p><p className="mt-1 text-sm font-medium">{profile?.username ?? tr("加载中...", "Loading...")}</p></section>
    <section className="mt-4 rounded-lg border border-[var(--border)] bg-[var(--card)] p-5"><div className="flex items-center gap-2"><KeyRound size={16} /><h2 className="text-sm font-semibold">{tr("修改密码", "Change password")}</h2></div><form className="mt-4 space-y-3" onSubmit={(event) => void updatePassword(event)}><input type="password" required value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} placeholder={tr("当前密码", "Current password")} className="h-10 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-3 text-sm"/><input type="password" required minLength={8} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} placeholder={tr("新密码（至少 8 位）", "New password (at least 8 characters)")} className="h-10 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-3 text-sm"/><button disabled={busy} className="h-10 rounded-md bg-[var(--primary)] px-4 text-sm font-medium text-[var(--primary-foreground)] disabled:opacity-50">{tr("更新密码", "Update password")}</button></form></section>
    <section className="mt-4 flex flex-col gap-3 rounded-lg border border-[var(--border)] bg-[var(--card)] p-5 sm:flex-row sm:items-center sm:justify-between"><div><h2 className="text-sm font-semibold">{tr("退出登录", "Sign out")}</h2><p className="mt-1 text-sm text-[var(--muted-foreground)]">{tr("结束此设备上的会话。", "End the session on this device.")}</p></div><button onClick={() => void signOut()} className="inline-flex h-9 w-fit items-center gap-2 rounded-md border border-[var(--border)] px-3 text-sm"><LogOut size={15} />{tr("退出", "Sign out")}</button></section>
    <section className="mt-4 rounded-lg border border-red-500/30 bg-[var(--card)] p-5"><div className="flex items-center gap-2 text-red-500"><Trash2 size={16} /><h2 className="text-sm font-semibold">{tr("注销账户", "Delete account")}</h2></div><p className="mt-2 text-sm text-[var(--muted-foreground)]">{tr("会永久删除你的私有学习数据和当前会话，无法恢复。", "Permanently deletes your private learning data and current sessions. This cannot be undone.")}</p><form className="mt-4 space-y-3" onSubmit={(event) => void removeAccount(event)}><input type="password" required value={deletePassword} onChange={(event) => setDeletePassword(event.target.value)} placeholder={tr("当前密码", "Current password")} className="h-10 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-3 text-sm"/><input required value={confirmation} onChange={(event) => setConfirmation(event.target.value)} placeholder={tr("输入 DELETE 确认", "Type DELETE to confirm")} className="h-10 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-3 text-sm"/><button disabled={busy} className="h-10 rounded-md border border-red-500/40 px-4 text-sm font-medium text-red-500 disabled:opacity-50">{tr("永久注销", "Delete permanently")}</button></form></section>
  </div>;
}
