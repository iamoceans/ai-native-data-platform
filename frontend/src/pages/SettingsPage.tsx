import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { changePassword, me, updateProfile, type Profile } from "../api/endpoints";
import { ErrorMessage } from "../components/ErrorMessage";
import { t, useLocale } from "../i18n";

const capabilityNames: Record<string, string> = {
  "query.submit": "运行查询", "metadata.read": "浏览数据目录",
  "saved_queries.manage": "管理已保存查询", "permission.request": "申请数据权限",
  "analysis.create": "创建分析", "admin.manage": "平台管理",
};

export function SettingsPage() {
  useLocale();
  const profile = useQuery({ queryKey: ["me"], queryFn: me });
  if (profile.isError) return <p className="notice error"><ErrorMessage error={profile.error} /></p>;
  if (!profile.data) return <p>{t("加载中…")}</p>;
  return <SettingsForm key={profile.data.id} profile={profile.data} reload={() => profile.refetch()} />;
}

function fields(profile: Profile) {
  return { displayName: profile.display_name ?? "", locale: profile.locale ?? "", timezone: profile.timezone ?? "", version: profile.settings_version ?? 1 };
}

function SettingsForm({ profile, reload }: { profile: Profile; reload: () => Promise<unknown> }) {
  useLocale();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(() => fields(profile));
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState(false);
  useEffect(() => { if (!dirty) setDraft(fields(profile)); }, [profile, dirty]);
  const save = useMutation({
    mutationFn: () => updateProfile({ display_name: draft.displayName, locale: draft.locale ? draft.locale as "en" | "zh-CN" : null,
      timezone: draft.timezone || null, expected_settings_version: draft.version }),
    onSuccess: updated => { queryClient.setQueryData(["me"], updated); setDraft(fields(updated)); setDirty(false); setSaved(true); },
  });
  const edit = (patch: Partial<typeof draft>) => { setDraft(value => ({ ...value, ...patch })); setDirty(true); setSaved(false); save.reset(); };
  return (
    <div className="stack settings-page" data-testid="settings-page">
      <header className="page-head"><h1>{t("个人设置")}</h1><p className="muted">{t("设置随账户保存，可在其他设备登录后恢复。")}</p></header>
      <section className="card stack"><h2>{t("账户信息")}</h2>
        <dl className="kv"><dt>{t("Username")}</dt><dd data-testid="settings-username">{profile.username}</dd>
          <dt>{t("角色")}</dt><dd>{profile.roles.join(", ")}</dd>
          <dt>{t("权限")}</dt><dd>{profile.capabilities.map(capability => t(capabilityNames[capability] ?? capability)).join(" · ") || "—"}</dd></dl>
      </section>
      <form className="card stack" onSubmit={event => { event.preventDefault(); save.mutate(); }}>
        <h2>{t("显示与偏好")}</h2>
        <label>{t("显示名称")}<input data-testid="settings-display-name" maxLength={80} autoComplete="nickname" value={draft.displayName} disabled={save.isPending} onChange={event => edit({ displayName: event.target.value })} /></label>
        <label>{t("界面语言")}<select data-testid="settings-locale" value={draft.locale} disabled={save.isPending} onChange={event => edit({ locale: event.target.value })}>
          <option value="">{t("跟随浏览器")}</option><option value="zh-CN">简体中文</option><option value="en">English</option>
        </select></label>
        <label>{t("时区")}<input data-testid="settings-timezone" list="settings-timezones" maxLength={128} value={draft.timezone} disabled={save.isPending} placeholder={Intl.DateTimeFormat().resolvedOptions().timeZone} onChange={event => edit({ timezone: event.target.value })} /></label>
        <datalist id="settings-timezones">{["UTC", "Asia/Singapore", "Asia/Shanghai", "Asia/Tokyo", "Europe/London", "America/New_York", "America/Los_Angeles"].map(zone => <option key={zone} value={zone} />)}</datalist>
        <p className="muted small">{t("时区留空时跟随浏览器，仅影响时间显示，不改变分析期间。")}</p>
        {save.isError ? <div className="notice error" role="alert" data-testid="settings-error"><ErrorMessage error={save.error} /><p>{t("如其他页面已更新设置，请重新加载后再保存。")}</p></div> : null}
        {saved ? <p role="status" data-testid="settings-saved">{t("设置已保存")}</p> : null}
        <div className="row-actions">
          <button type="submit" className="primary" data-testid="settings-save" disabled={!dirty || save.isPending}>{save.isPending ? t("保存中…") : t("保存设置")}</button>
          <button type="button" className="ghost" disabled={save.isPending} onClick={async () => { await reload(); setDirty(false); save.reset(); setSaved(false); }}>{t("重新加载设置")}</button>
        </div>
      </form>
      <PasswordForm />
    </div>
  );
}

function PasswordForm() {
  useLocale();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const change = useMutation({
    mutationFn: () => changePassword(current, next),
    onSuccess: () => { setCurrent(""); setNext(""); setConfirm(""); client.clear(); navigate("/login", { replace: true }); },
  });
  return <form className="card stack" onSubmit={event => { event.preventDefault(); change.mutate(); }}>
    <h2>{t("修改密码")}</h2><p className="muted">{t("修改后将退出所有设备，请使用新密码重新登录。")}</p>
    <label>{t("当前密码")}<input type="password" autoComplete="current-password" data-testid="password-current" value={current} maxLength={1024} disabled={change.isPending} onChange={event => { setCurrent(event.target.value); change.reset(); }} required /></label>
    <label>{t("新密码（至少 12 个字符）")}<input type="password" autoComplete="new-password" data-testid="password-new" value={next} minLength={12} maxLength={1024} disabled={change.isPending} onChange={event => { setNext(event.target.value); change.reset(); }} required /></label>
    <label>{t("确认新密码")}<input type="password" autoComplete="new-password" data-testid="password-confirm" value={confirm} disabled={change.isPending} onChange={event => setConfirm(event.target.value)} required /></label>
    {confirm && confirm !== next ? <p className="notice error">{t("两次输入的新密码不一致。")}</p> : null}
    {change.isError ? <p className="notice error" role="alert" data-testid="password-error"><ErrorMessage error={change.error} /></p> : null}
    <button type="submit" className="primary" data-testid="password-submit" disabled={change.isPending || !current || next.length < 12 || next !== confirm || next === current}>{t("修改密码并退出")}</button>
  </form>;
}
