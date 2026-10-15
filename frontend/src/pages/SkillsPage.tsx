import { ErrorMessage } from "../components/ErrorMessage";
import { t, useLocale, message, translateMessage, type Message } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { ApiError } from "../api/client";
import {
  createSkill,
  createSkillVersion,
  listSkills,
  patchSkill,
  previewSkill,
  type SkillPreview,
  type SkillSummary,
} from "../api/endpoints";
import { PermissionGate } from "../components/PermissionGate";

/**
 * Skill library: upload a SKILL.md or a ZIP bundle, preview the validated
 * result, then save it. Builtin skills are read-only; an uploaded one can be
 * disabled or archived by its owner (never edited - versions are immutable).
 */
export function SkillsPage() {
  useLocale();
  const queryClient = useQueryClient();
  const [includeArchived, setIncludeArchived] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<SkillPreview | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [notice, setNotice] = useState<Message | null>(null);

  const skills = useQuery({
    queryKey: ["skills", includeArchived],
    queryFn: () => listSkills(includeArchived),
  });

  const runPreview = useMutation({
    mutationFn: (candidate: File) => previewSkill(candidate),
    onSuccess: (result) => {
      setPreview(result);
      setError(null);
      setNotice(null);
    },
    onError: (failure) => {
      setPreview(null);
      setError(failure instanceof ApiError ? failure : null);
    },
  });

  const save = useMutation({
    mutationFn: (input: { file: File; hash: string }) =>
      createSkill(input.file, input.hash),
    onSuccess: (skill) => {
      setNotice(
        message("已保存 {0} v{1}", {
          0: skill.slug,
          1: skill.latest_version?.version ?? 1,
        }),
      );
      setPreview(null);
      setFile(null);
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["skills"] });
    },
    onError: (failure) =>
      setError(failure instanceof ApiError ? failure : null),
  });

  const addVersion = useMutation({
    mutationFn: (input: { skillId: string; file: File; hash: string }) =>
      createSkillVersion(input.skillId, input.file, input.hash),
    onSuccess: (version) => {
      setNotice(message("已新增版本 v{0}", { 0: version.version }));
      setPreview(null);
      setFile(null);
      queryClient.invalidateQueries({ queryKey: ["skills"] });
    },
    onError: (failure) =>
      setError(failure instanceof ApiError ? failure : null),
  });

  const update = useMutation({
    mutationFn: (input: {
      skillId: string;
      patch: { enabled?: boolean; archived?: boolean };
    }) => patchSkill(input.skillId, input.patch),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["skills"] }),
    onError: (failure) =>
      setError(failure instanceof ApiError ? failure : null),
  });

  const onPick = (candidate: File) => {
    setFile(candidate);
    setPreview(null);
    setError(null);
    setNotice(null);
    runPreview.mutate(candidate);
  };

  return (
    <PermissionGate capability="analysis.create">
      <div className="skills">
        <header className="page-head">
          <h1>{t("Skills")}</h1>
          <p className="muted">
            {t(
              "上传分析方法包（SKILL.md 或包含 SKILL.md 的 ZIP）。保存的版本不可修改， 启用后才会参与会话匹配；内置技能只读。",
            )}
          </p>
        </header>

        <section className="card">
          <div className="card-head">
            <h2>{t("上传")}</h2>
            <span className="muted">{t("先预览校验结果，再确认保存")}</span>
          </div>
          <div className="form-row">
            <div className="file-pick">
              <FileButton
                label={t("选择文件")}
                accept=".md,.zip"
                className="ghost"
                buttonTestId="skill-pick-file"
                inputTestId="skill-file"
                onPick={onPick}
              />
              <span
                className={file ? "file-name picked" : "file-name"}
                title={file?.name}
                data-testid="skill-file-name"
              >
                {file ? file.name : t("未选择文件")}
              </span>
            </div>
            <button
              type="button"
              className="primary"
              data-testid="skill-preview"
              disabled={!file || runPreview.isPending}
              onClick={() => file && runPreview.mutate(file)}
            >
              {runPreview.isPending ? t("校验中…") : t("重新校验")}
            </button>
          </div>

          {error ? (
            <p className="notice error" data-testid="skill-error">
              <strong>{error.code}</strong> — <ErrorMessage error={error} />
            </p>
          ) : null}
          {notice ? (
            <p className="notice ok" data-testid="skill-notice">
              {translateMessage(notice)}
            </p>
          ) : null}

          {preview ? (
            <div className="skill-preview" data-testid="skill-preview-result">
              <dl className="kv">
                <dt>{t("名称")}</dt>
                <dd>{preview.name}</dd>
                <dt>{t("说明")}</dt>
                <dd>{preview.description}</dd>
                <dt>{t("适用性")}</dt>
                <dd>
                  {preview.compatibility.applicable
                    ? t("适用于当前环境")
                    : t("当前环境不适用")}
                  {preview.compatibility.unknown_metric_keys?.length
                    ? t("（未知指标：{0}）", {
                        0: preview.compatibility.unknown_metric_keys.join("、"),
                      })
                    : ""}
                  {preview.compatibility.invalid_dimensions
                    ? Object.entries(
                        preview.compatibility.invalid_dimensions,
                      ).map(([metric, dims]) =>
                        t("（{0} 不支持的维度：{1}）", {
                          0: metric,
                          1: dims.join("、"),
                        }),
                      )
                    : ""}
                </dd>
                <dt>{t("内容哈希")}</dt>
                <dd>
                  <code>{preview.content_hash.slice(0, 16)}…</code>
                </dd>
                <dt>{t("文件")}</dt>
                <dd>
                  {preview.files.map((item) => (
                    <span key={item.path} className="tag">
                      {item.path}
                    </span>
                  ))}
                </dd>
              </dl>
              <pre className="skill-body">{preview.body_preview}</pre>
              {preview.body_truncated ? (
                <p className="muted">{t("（预览截断，保存后完整保留）")}</p>
              ) : null}
              <div className="row-actions">
                <button
                  type="button"
                  className="primary"
                  data-testid="skill-save"
                  disabled={!file || save.isPending}
                  onClick={() =>
                    file && save.mutate({ file, hash: preview.content_hash })
                  }
                >
                  {save.isPending ? t("保存中…") : t("保存为新技能")}
                </button>
              </div>
            </div>
          ) : null}
        </section>

        <section className="card">
          <div className="card-head">
            <h2>{t("技能库")}</h2>
            <label className="inline-check">
              <input
                type="checkbox"
                data-testid="skill-include-archived"
                checked={includeArchived}
                onChange={(event) => setIncludeArchived(event.target.checked)}
              />
              {t("显示已归档")}
            </label>
          </div>
          {skills.isLoading ? <p className="muted">{t("加载中…")}</p> : null}
          {skills.data?.length === 0 ? (
            <p className="muted">{t("还没有技能。")}</p>
          ) : null}
          <ul className="skill-list" data-testid="skill-list">
            {skills.data?.map((skill) => (
              <SkillRow
                key={skill.id}
                skill={skill}
                busy={update.isPending}
                onToggle={(patch) =>
                  update.mutate({ skillId: skill.id, patch })
                }
                onNewVersion={(candidate) => {
                  setFile(candidate);
                  setError(null);
                  setNotice(null);
                  previewSkill(candidate)
                    .then((result) => {
                      setPreview(result);
                      addVersion.mutate({
                        skillId: skill.id,
                        file: candidate,
                        hash: result.content_hash,
                      });
                    })
                    .catch((failure) => {
                      setPreview(null);
                      setError(failure instanceof ApiError ? failure : null);
                    });
                }}
              />
            ))}
          </ul>
        </section>
      </div>
    </PermissionGate>
  );
}

/**
 * A file picker drawn with the workspace's own controls: the native
 * `<input type="file">` stays hidden and a real button opens it, so the visible
 * control is the shared button style instead of the browser's own chrome.
 */
function FileButton({
  label,
  accept,
  className,
  buttonTestId,
  inputTestId,
  disabled,
  onPick,
}: {
  label: string;
  accept: string;
  className: string;
  buttonTestId: string;
  inputTestId: string;
  disabled?: boolean;
  onPick: (file: File) => void;
}) {
  useLocale();
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        hidden
        data-testid={inputTestId}
        onChange={(event) => {
          const picked = event.target.files?.[0];
          // Clear the selection so picking the same file again still fires.
          event.target.value = "";
          if (picked) onPick(picked);
        }}
      />
      <button
        type="button"
        className={className}
        data-testid={buttonTestId}
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
      >
        {label}
      </button>
    </>
  );
}

function SkillRow({
  skill,
  busy,
  onToggle,
  onNewVersion,
}: {
  skill: SkillSummary;
  busy: boolean;
  onToggle: (patch: { enabled?: boolean; archived?: boolean }) => void;
  onNewVersion: (file: File) => void;
}) {
  useLocale();
  const latest = skill.latest_version;
  const compatibility = (latest?.compatibility ?? {}) as {
    applicable?: boolean;
  };
  return (
    <li className="skill-row" data-testid={`skill-${skill.slug}`}>
      <div className="skill-head">
        <strong>{skill.slug}</strong>
        <span className={`tag ${skill.source === "builtin" ? "" : "warn"}`}>
          {skill.source === "builtin" ? t("内置") : t("自建")}
        </span>
        <span className={`badge ${skill.enabled ? "" : "warn"}`}>
          {skill.enabled ? t("已启用") : t("已停用")}
        </span>
        {skill.archived_at ? (
          <span className="badge warn">{t("已归档")}</span>
        ) : null}
        {compatibility.applicable === false ? (
          <span
            className="badge warn"
            title={t("当前环境缺少声明的指标或维度")}
          >
            {t("不适用")}
          </span>
        ) : null}
        <span className="muted">
          {t("skills.versions", { count: skill.version_count })}
          {latest ? t(" · 最新 v{0}", { 0: latest.version }) : ""}
        </span>
      </div>
      {latest ? (
        <p className="muted">
          {String(
            (latest.manifest as { description?: string }).description ?? "",
          )}
          {" · "}
          <code>{latest.content_hash.slice(0, 12)}…</code>
        </p>
      ) : null}
      <div className="row-actions">
        {skill.source === "upload" ? (
          <>
            <button
              type="button"
              className="ghost small"
              disabled={busy}
              data-testid={`skill-toggle-${skill.slug}`}
              onClick={() => onToggle({ enabled: !skill.enabled })}
            >
              {skill.enabled ? t("停用") : t("启用")}
            </button>
            <button
              type="button"
              className="ghost small"
              disabled={busy}
              data-testid={`skill-archive-${skill.slug}`}
              onClick={() => onToggle({ archived: !skill.archived_at })}
            >
              {skill.archived_at ? t("恢复") : t("归档")}
            </button>
            <FileButton
              label={t("新版本")}
              accept=".md,.zip"
              className="ghost small"
              buttonTestId={`skill-new-version-${skill.slug}`}
              inputTestId={`skill-new-version-input-${skill.slug}`}
              disabled={busy}
              onPick={onNewVersion}
            />
          </>
        ) : (
          <span className="muted">{t("内置技能由仓库维护，只读")}</span>
        )}
      </div>
    </li>
  );
}
