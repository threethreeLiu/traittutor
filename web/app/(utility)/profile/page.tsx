"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, KeyRound, LogOut, Trash2 } from "lucide-react";
import { fetchAuthStatus, logout } from "@/lib/auth";
import { changeAccountPassword, deleteAccount, getProfile, type ProfileInfo } from "@/lib/profile-api";

export default function AccountPage() {
  const router = useRouter();
  const [profile, setProfile] = useState<ProfileInfo | null>(null);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [deletePassword, setDeletePassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => { void fetchAuthStatus().then(async (status) => { if (!status?.enabled || !status.authenticated) { router.replace("/login"); return; } try { setProfile(await getProfile()); } catch { setError("无法读取账户信息"); } }); }, [router]);
  async function signOut() { await logout(); router.replace("/login"); }
  async function updatePassword(event: React.FormEvent) { event.preventDefault(); setBusy(true); setError(""); setMessage(""); try { await changeAccountPassword(currentPassword, newPassword); setCurrentPassword(""); setNewPassword(""); setMessage("密码已更新"); } catch (cause) { setError(cause instanceof Error ? cause.message : "修改密码失败"); } finally { setBusy(false); } }
  async function removeAccount(event: React.FormEvent) { event.preventDefault(); setBusy(true); setError(""); try { await deleteAccount(deletePassword, confirmation); router.replace("/login"); } catch (cause) { setError(cause instanceof Error ? cause.message : "注销账户失败"); setBusy(false); } }

  return <div className="h-screen overflow-y-auto bg-[var(--background)] px-4 py-10"><div className="mx-auto max-w-xl"><Link href="/" className="inline-flex items-center gap-1.5 text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)]"><ArrowLeft size={16} />返回</Link><header className="mt-5 border-b border-[var(--border)] pb-5"><h1 className="text-xl font-semibold">账户与隐私</h1><p className="mt-1 text-sm text-[var(--muted-foreground)]">管理登录凭据和账户数据。</p></header>{error ? <p className="mt-4 rounded-md bg-red-500/10 px-3 py-2 text-sm text-red-500">{error}</p> : null}{message ? <p className="mt-4 rounded-md bg-emerald-500/10 px-3 py-2 text-sm text-emerald-600">{message}</p> : null}
  <section className="mt-5 rounded-lg border border-[var(--border)] bg-[var(--card)] p-5"><p className="text-xs text-[var(--muted-foreground)]">登录邮箱</p><p className="mt-1 text-sm font-medium">{profile?.username ?? "加载中..."}</p></section>
  <section className="mt-4 rounded-lg border border-[var(--border)] bg-[var(--card)] p-5"><div className="flex items-center gap-2"><KeyRound size={16} /><h2 className="text-sm font-semibold">修改密码</h2></div><form className="mt-4 space-y-3" onSubmit={(event) => void updatePassword(event)}><input type="password" required value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} placeholder="当前密码" className="h-10 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-3 text-sm"/><input type="password" required minLength={8} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} placeholder="新密码（至少 8 位）" className="h-10 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-3 text-sm"/><button disabled={busy} className="h-10 rounded-md bg-[var(--primary)] px-4 text-sm font-medium text-[var(--primary-foreground)] disabled:opacity-50">更新密码</button></form></section>
  <section className="mt-4 flex items-center justify-between rounded-lg border border-[var(--border)] bg-[var(--card)] p-5"><div><h2 className="text-sm font-semibold">退出登录</h2><p className="mt-1 text-sm text-[var(--muted-foreground)]">结束此设备上的会话。</p></div><button onClick={() => void signOut()} className="inline-flex h-9 items-center gap-2 rounded-md border border-[var(--border)] px-3 text-sm"><LogOut size={15} />退出</button></section>
  <section className="mt-4 rounded-lg border border-red-500/30 bg-[var(--card)] p-5"><div className="flex items-center gap-2 text-red-500"><Trash2 size={16} /><h2 className="text-sm font-semibold">注销账户</h2></div><p className="mt-2 text-sm text-[var(--muted-foreground)]">会永久删除你的私有学习数据和当前会话，无法恢复。</p><form className="mt-4 space-y-3" onSubmit={(event) => void removeAccount(event)}><input type="password" required value={deletePassword} onChange={(event) => setDeletePassword(event.target.value)} placeholder="当前密码" className="h-10 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-3 text-sm"/><input required value={confirmation} onChange={(event) => setConfirmation(event.target.value)} placeholder='输入 DELETE 确认' className="h-10 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-3 text-sm"/><button disabled={busy} className="h-10 rounded-md border border-red-500/40 px-4 text-sm font-medium text-red-500 disabled:opacity-50">永久注销</button></form></section></div></div>;
}
