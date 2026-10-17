import { ErrorMessage } from "../components/ErrorMessage";
import { datasetName } from "../components/datasetName";
import { statusLabel } from "../components/StatusBadge";
import { t, useLocale, message, translateMessage, type Message } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError } from "../api/client";
import {
  approvePermissionRequest,
  createAdminRole,
  createAdminUser,
  createGrant,
  deleteGrant,
  listAdminRoles,
  listAdminUsers,
  listAudits,
  listDatasets,
  listGrants,
  listPermissionRequests,
  mockApprovePermissionRequest,
  rejectPermissionRequest,
  updateUserRoles,
  listAdminTeams, createAdminTeam, updateUserTeam,
  type PermissionRequest,
} from "../api/endpoints";
import { PermissionGate } from "../components/PermissionGate";

/**
 * Permissions (spec section 24): role grants, the access-request queue and the
 * audit trail.
 *
 * The screen keeps "real grant" and "mock state" visibly apart: a request marked
 * MOCK_APPROVED is labelled as a mock that granted nothing, and grants are only
 * created through the grant form (which bumps the policy revision and can cancel
 * live queries, spec 12/22).
 */
export function AdminPermissionsPage() {
  useLocale();
  const queryClient = useQueryClient();
  const [requestFilter, setRequestFilter] = useState<
    "ALL" | PermissionRequest["status"]
  >("ALL");
  const [decision, setDecision] = useState({
    roleId: "",
    action: "query" as "discover" | "query",
  });
  const [userForm, setUserForm] = useState({
    username: "",
    password: "",
    roleIds: [] as string[],
  });
  const [roleName, setRoleName] = useState("");
  const [teamName, setTeamName] = useState("");
  const teams = useQuery({ queryKey: ["admin-teams"], queryFn: listAdminTeams });
  const createdTeam = useMutation({ mutationFn: () => createAdminTeam(teamName.trim()),
    onSuccess: () => { setTeamName(""); setError(null); queryClient.invalidateQueries({ queryKey: ["admin-teams"] }); },
    onError: (failure: unknown) => onFailure(failure) });
  const assignedTeam = useMutation({ mutationFn: (input: { userId: string; teamId: string | null }) => updateUserTeam(input.userId, input.teamId),
    onSuccess: () => { invalidate(); queryClient.invalidateQueries({ queryKey: ["memory"] }); },
    onError: (failure: unknown) => onFailure(failure) });
  const [grantForm, setGrantForm] = useState({
    roleId: "",
    datasetId: "",
    action: "query" as "discover" | "query",
  });
  const [error, setError] = useState<ApiError | null>(null);
  const [notice, setNotice] = useState<Message | null>(null);

  const users = useQuery({
    queryKey: ["admin-users"],
    queryFn: listAdminUsers,
  });
  const roles = useQuery({
    queryKey: ["admin-roles"],
    queryFn: listAdminRoles,
  });
  const grants = useQuery({ queryKey: ["admin-grants"], queryFn: listGrants });
  const requests = useQuery({
    queryKey: ["admin-permission-requests", requestFilter],
    queryFn: () =>
      listPermissionRequests(
        requestFilter === "ALL" ? undefined : requestFilter,
      ),
  });
  const datasets = useQuery({
    queryKey: ["datasets", {}],
    queryFn: () => listDatasets(),
  });
  const audits = useQuery({ queryKey: ["admin-audits"], queryFn: listAudits });

  const datasetLabel = (datasetId: string) => {
    const dataset = (datasets.data?.items ?? []).find((item) => item.id === datasetId);
    return dataset ? datasetName(dataset) : datasetId.slice(0, 8);
  };
  const roleLabel = (roleId: string) =>
    (roles.data ?? []).find((role) => role.id === roleId)?.name ??
    roleId.slice(0, 8);
  const userLabel = (userId: string) =>
    (users.data?.items ?? []).find((user) => user.id === userId)?.username ??
    userId.slice(0, 8);

  const onFailure = (failure: unknown) => {
    setError(failure instanceof ApiError ? failure : null);
    setNotice(null);
  };
  const invalidate = () => {
    setError(null);
    for (const key of [
      "admin-users",
      "admin-roles",
      "admin-grants",
      "admin-permission-requests",
      "admin-audits",
    ]) {
      queryClient.invalidateQueries({ queryKey: [key] });
    }
  };

  const createdUser = useMutation({
    mutationFn: () =>
      createAdminUser({
        username: userForm.username,
        password: userForm.password,
        role_ids: userForm.roleIds,
      }),
    onSuccess: (user) => {
      setNotice(
        message("已创建用户 {0}（初始密码不会写入日志）", { 0: user.username }),
      );
      setUserForm({ username: "", password: "", roleIds: [] });
      invalidate();
    },
    onError: onFailure,
  });

  const assigned = useMutation({
    mutationFn: (input: { userId: string; roleIds: string[] }) =>
      updateUserRoles(input.userId, input.roleIds),
    onSuccess: (user) => {
      setNotice(
        message("已更新 {0} 的角色；策略 revision 已递增", {
          0: user.username,
        }),
      );
      invalidate();
    },
    onError: onFailure,
  });

  const createdRole = useMutation({
    mutationFn: () => createAdminRole(roleName),
    onSuccess: (role) => {
      setNotice(
        message("已创建角色 {0}（能力来自角色名映射）", { 0: role.name }),
      );
      setRoleName("");
      invalidate();
    },
    onError: onFailure,
  });

  const granted = useMutation({
    mutationFn: () =>
      createGrant({
        role_id: grantForm.roleId,
        dataset_id: grantForm.datasetId,
        action: grantForm.action,
      }),
    onSuccess: () => {
      setNotice(message("已创建真实授权（默认拒绝之外的白名单）"));
      invalidate();
    },
    onError: onFailure,
  });

  const revoked = useMutation({
    mutationFn: (grantId: string) => deleteGrant(grantId),
    onSuccess: () => {
      setNotice(message("已撤回授权；相关活动查询会被取消"));
      invalidate();
    },
    onError: onFailure,
  });

  const approved = useMutation({
    mutationFn: (requestId: string) =>
      approvePermissionRequest(requestId, {
        role_id: decision.roleId,
        action: decision.action,
      }),
    onSuccess: (request) => {
      setNotice(
        message(
          "申请 {0} 已批准，并创建了真实授权（{1}）—— 策略 revision 已递增",
          { 0: request.id.slice(0, 8), 1: decision.action },
        ),
      );
      invalidate();
    },
    onError: onFailure,
  });

  const rejected = useMutation({
    mutationFn: (requestId: string) =>
      rejectPermissionRequest(requestId, t("管理员拒绝")),
    onSuccess: (request) => {
      setNotice(
        message("申请 {0} 已拒绝 —— 未创建任何授权", {
          0: request.id.slice(0, 8),
        }),
      );
      invalidate();
    },
    onError: onFailure,
  });

  const mockApproved = useMutation({
    mutationFn: (requestId: string) => mockApprovePermissionRequest(requestId),
    onSuccess: (request) => {
      setNotice(
        message("申请 {0} 已标记 {1} —— 这是 Mock 状态，未创建任何授权", {
          0: request.id.slice(0, 8),
          1: request.status,
        }),
      );
      invalidate();
    },
    onError: onFailure,
  });

  const realGrants = grants.data?.items ?? [];
  const mockApprovedCount = realGrants.length;

  return (
    <PermissionGate capability="admin.manage">
      <div className="workspace" data-testid="admin-permissions">
        <section className="card">
          <div className="card-head">
            <div>
              <span className="eyebrow">{t("Administration")}</span>
              <h1>{t("Permissions")}</h1>
            </div>
            <span className="muted small">
              {t("真实授权")} {mockApprovedCount} {t("条 · 申请队列")}{" "}
              {requests.data?.items.length ?? 0} {t("条")}
            </span>
          </div>
          {error ? (
            <p className="notice error" role="alert">
              <strong>{error.code}</strong> — <ErrorMessage error={error} />
            </p>
          ) : null}
          {notice ? (
            <p className="notice" data-testid="admin-notice">
              {translateMessage(notice)}
            </p>
          ) : null}
        </section>

        <section className="card">
          <div className="card-head">
            <h2>{t("访问申请队列")}</h2>
            <label className="inline-label">
              {t("状态")}{" "}
              <select
                data-testid="request-status-filter"
                value={requestFilter}
                onChange={(event) =>
                  setRequestFilter(event.target.value as typeof requestFilter)
                }
              >
                <option value="ALL">{t("全部")}</option>
                <option value="REQUESTED">{statusLabel("REQUESTED")}</option>
                <option value="MOCK_APPROVED">
                  {statusLabel("MOCK_APPROVED")}
                </option>
                <option value="REJECTED">{statusLabel("REJECTED")}</option>
              </select>
            </label>
          </div>
          <div className="form-row">
            <label>
              {t("批准时授予的角色")}{" "}
              <select
                data-testid="decision-role"
                value={decision.roleId}
                onChange={(event) =>
                  setDecision((current) => ({
                    ...current,
                    roleId: event.target.value,
                  }))
                }
              >
                <option value="">{t("选择角色…")}</option>
                {(roles.data ?? []).map((role) => (
                  <option key={role.id} value={role.id}>
                    {role.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("动作")}{" "}
              <select
                data-testid="decision-action"
                value={decision.action}
                onChange={(event) =>
                  setDecision((current) => ({
                    ...current,
                    action: event.target.value as "discover" | "query",
                  }))
                }
              >
                <option value="query">{t("query（含 discover）")}</option>
                <option value="discover">{t("discover")}</option>
              </select>
            </label>
            <span className="muted small">
              {t("「批准并授权」创建真实授权；「标记 Mock」只改状态，不授权")}
            </span>
          </div>
          <table className="data-table" data-testid="request-table">
            <thead>
              <tr>
                <th scope="col">{t("申请人")}</th>
                <th scope="col">{t("数据集")}</th>
                <th scope="col">{t("理由")}</th>
                <th scope="col">{t("状态")}</th>
                <th scope="col">{t("操作")}</th>
              </tr>
            </thead>
            <tbody>
              {(requests.data?.items ?? []).map((request) => (
                <tr key={request.id}>
                  <td>{userLabel(request.user_id)}</td>
                  <td>{datasetLabel(request.dataset_id)}</td>
                  <td className="small">{request.reason}</td>
                  <td>
                    <span
                      className={`badge ${
                        request.status === "APPROVED"
                          ? "ok"
                          : request.status === "MOCK_APPROVED"
                            ? "wait"
                            : "muted"
                      }`}
                      data-testid={`request-status-${request.id}`}
                    >
                      {statusLabel(request.status)}
                    </span>
                    {request.status === "MOCK_APPROVED" ? (
                      <span className="muted small">
                        {" "}
                        {t("Mock 标记，未授权")}
                      </span>
                    ) : null}
                    {request.status === "APPROVED" ? (
                      <span className="muted small">
                        {" "}
                        {t("已创建真实授权")}
                      </span>
                    ) : null}
                  </td>
                  <td className="row-actions">
                    <button
                      className="primary small"
                      data-testid={`approve-${request.id}`}
                      disabled={
                        request.status !== "REQUESTED" ||
                        approved.isPending ||
                        !decision.roleId
                      }
                      onClick={() => approved.mutate(request.id)}
                    >
                      {t("批准并授权")}
                    </button>
                    <button
                      className="ghost small"
                      data-testid={`reject-${request.id}`}
                      disabled={
                        request.status === "APPROVED" || rejected.isPending
                      }
                      onClick={() => rejected.mutate(request.id)}
                    >
                      {t("拒绝")}
                    </button>
                    <button
                      className="ghost small"
                      data-testid={`mock-approve-${request.id}`}
                      disabled={
                        request.status !== "REQUESTED" || mockApproved.isPending
                      }
                      onClick={() => mockApproved.mutate(request.id)}
                    >
                      {t("标记 Mock（不授权）")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!requests.data?.items.length ? (
            <p className="muted small">{t("当前筛选下没有申请。")}</p>
          ) : null}
        </section>

        <section className="card">
          <div className="card-head">
            <h2>{t("角色授权（默认拒绝）")}</h2>
            <span className="muted small">
              {t("授权变更会递增 policy revision")}
            </span>
          </div>
          <div className="form-row">
            <label>
              {t("角色")}{" "}
              <select
                data-testid="grant-role"
                value={grantForm.roleId}
                onChange={(event) =>
                  setGrantForm((current) => ({
                    ...current,
                    roleId: event.target.value,
                  }))
                }
              >
                <option value="">{t("选择角色…")}</option>
                {(roles.data ?? []).map((role) => (
                  <option key={role.id} value={role.id}>
                    {role.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="grow">
              {t("数据集")}{" "}
              <select
                data-testid="grant-dataset"
                value={grantForm.datasetId}
                onChange={(event) =>
                  setGrantForm((current) => ({
                    ...current,
                    datasetId: event.target.value,
                  }))
                }
              >
                <option value="">{t("选择数据集…")}</option>
                {(datasets.data?.items ?? []).map((dataset) => (
                  <option key={dataset.id} value={dataset.id}>
                    {datasetName(dataset)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("动作")}{" "}
              <select
                data-testid="grant-action"
                value={grantForm.action}
                onChange={(event) =>
                  setGrantForm((current) => ({
                    ...current,
                    action: event.target.value as "discover" | "query",
                  }))
                }
              >
                <option value="discover">{t("discover")}</option>
                <option value="query">{t("query")}</option>
              </select>
            </label>
            <button
              className="primary"
              data-testid="create-grant"
              disabled={
                granted.isPending || !grantForm.roleId || !grantForm.datasetId
              }
              onClick={() => granted.mutate()}
            >
              {t("创建授权")}
            </button>
          </div>
          <table className="data-table" data-testid="grant-table">
            <thead>
              <tr>
                <th scope="col">{t("角色")}</th>
                <th scope="col">{t("数据集")}</th>
                <th scope="col">{t("动作")}</th>
                <th scope="col">{t("到期")}</th>
                <th scope="col">{t("操作")}</th>
              </tr>
            </thead>
            <tbody>
              {realGrants.map((grant) => (
                <tr key={grant.id}>
                  <td>{roleLabel(grant.role_id)}</td>
                  <td>{datasetLabel(grant.dataset_id)}</td>
                  <td>{grant.action}</td>
                  <td className="small">{grant.expires_at ?? "—"}</td>
                  <td>
                    <button
                      className="ghost small"
                      data-testid={`revoke-${grant.id}`}
                      disabled={revoked.isPending}
                      onClick={() => revoked.mutate(grant.id)}
                    >
                      {t("撤回")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="card">
          <div className="card-head">
            <h2>{t("用户与角色")}</h2>
            <span className="muted small">
              {t("初始密码仅在此提交，不会写入日志")}
            </span>
          </div>
          <div className="form-row wrap">
            <label>
              {t("用户名")}{" "}
              <input
                data-testid="new-user-name"
                value={userForm.username}
                onChange={(event) =>
                  setUserForm((current) => ({
                    ...current,
                    username: event.target.value,
                  }))
                }
              />
            </label>
            <label className="grow">
              {t("初始密码")}{" "}
              <input
                type="password"
                data-testid="new-user-password"
                value={userForm.password}
                onChange={(event) =>
                  setUserForm((current) => ({
                    ...current,
                    password: event.target.value,
                  }))
                }
              />
            </label>
            <fieldset className="role-picker">
              <legend>{t("角色")}</legend>
              {(roles.data ?? []).map((role) => (
                <label className="check" key={role.id}>
                  <input
                    type="checkbox"
                    data-testid={`new-user-role-${role.name}`}
                    checked={userForm.roleIds.includes(role.id)}
                    onChange={(event) =>
                      setUserForm((current) => ({
                        ...current,
                        roleIds: event.target.checked
                          ? [...current.roleIds, role.id]
                          : current.roleIds.filter((item) => item !== role.id),
                      }))
                    }
                  />
                  {role.name}
                </label>
              ))}
            </fieldset>
            <button
              className="primary"
              data-testid="create-user"
              disabled={
                createdUser.isPending ||
                !userForm.username ||
                userForm.password.length < 12
              }
              onClick={() => createdUser.mutate()}
            >
              {t("创建用户")}
            </button>
          </div>
          <div className="form-row wrap">
            <label>{t("新团队名称")} <input data-testid="new-team-name" maxLength={128} value={teamName} onChange={event => setTeamName(event.target.value)} /></label>
            <button className="ghost" data-testid="create-team" disabled={createdTeam.isPending || !teamName.trim() || teams.isError}
              onClick={() => createdTeam.mutate()}>{t("创建团队")}</button>
          </div>
          {teams.isError ? <p className="notice warn">{t("团队加载失败")} <button onClick={() => teams.refetch()}>{t("重试")}</button></p> : null}
          <p className="muted small">{t("团队成员共享业务记忆，仍须具备原始数据权限；未加入团队时记忆仅自己可见。")}</p>
          <table className="data-table" data-testid="user-table">
            <thead>
              <tr>
                <th scope="col">{t("用户")}</th>
                <th scope="col">{t("角色")}</th>
                <th scope="col">{t("状态")}</th>
                <th scope="col">{t("调整角色")}</th>
                <th scope="col">{t("记忆团队")}</th>
              </tr>
            </thead>
            <tbody>
              {(users.data?.items ?? []).map((user) => (
                <tr key={user.id}>
                  <th scope="row">{user.username}</th>
                  <td>
                    {user.roles.map((role) => role.name).join(", ") || "—"}
                  </td>
                  <td>{user.active ? "active" : "inactive"}</td>
                  <td className="row-actions">
                    {(roles.data ?? []).map((role) => (
                      <label className="check small" key={role.id}>
                        <input
                          type="checkbox"
                          data-testid={`assign-${user.username}-${role.name}`}
                          checked={user.roles.some(
                            (item) => item.id === role.id,
                          )}
                          disabled={assigned.isPending}
                          onChange={(event) =>
                            assigned.mutate({
                              userId: user.id,
                              roleIds: event.target.checked
                                ? [
                                    ...user.roles.map((item) => item.id),
                                    role.id,
                                  ]
                                : user.roles
                                    .map((item) => item.id)
                                    .filter((item) => item !== role.id),
                            })
                          }
                        />
                        {role.name}
                      </label>
                    ))}
                  </td>
                  <td><select aria-label={t("{0} 的记忆团队", { 0: user.username })} data-testid={`team-${user.username}`}
                    value={user.team_id ?? ""} disabled={assignedTeam.isPending || teams.isPending || teams.isError}
                    onChange={event => assignedTeam.mutate({ userId: user.id, teamId: event.target.value || null })}>
                    <option value="">{t("个人记忆")}</option>
                    {(teams.data?.items ?? []).map(team => <option key={team.id} value={team.id}>{team.name}</option>)}
                  </select></td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="form-row">
            <label className="grow">
              {t("新角色名（小写字母开头）")}{" "}
              <input
                data-testid="new-role-name"
                value={roleName}
                onChange={(event) => setRoleName(event.target.value)}
              />
            </label>
            <button
              className="ghost"
              data-testid="create-role"
              disabled={
                createdRole.isPending || !/^[a-z][a-z0-9_-]*$/.test(roleName)
              }
              onClick={() => createdRole.mutate()}
            >
              {t("创建角色")}
            </button>
          </div>
          <ul className="capability-list">
            {(roles.data ?? []).map((role) => (
              <li key={role.id}>
                <code>{role.name}</code>{" "}
                <span className="muted small">
                  {role.capabilities.join(", ")}
                </span>
              </li>
            ))}
          </ul>
        </section>

        <section className="card">
          <div className="card-head">
            <h2>{t("审计（脱敏）")}</h2>
            <span className="muted small">
              {t("登录、授权、管理与取消操作")}
            </span>
          </div>
          <table className="data-table" data-testid="audit-table">
            <thead>
              <tr>
                <th scope="col">{t("时间")}</th>
                <th scope="col">{t("动作")}</th>
                <th scope="col">{t("对象")}</th>
                <th scope="col">{t("结果")}</th>
                <th scope="col">{t("trace")}</th>
              </tr>
            </thead>
            <tbody>
              {(audits.data?.items ?? []).slice(0, 20).map((entry) => (
                <tr key={entry.id}>
                  <td className="small">
                    {entry.created_at.replace("T", " ").slice(0, 19)}
                  </td>
                  <td>{entry.action}</td>
                  <td className="small">{entry.resource_type}</td>
                  <td>{entry.outcome}</td>
                  <td className="mono small">{entry.trace_id.slice(0, 8)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>
    </PermissionGate>
  );
}
