"""Evidence-bound report construction and validation."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any


class ReportValidationError(ValueError):
    pass


# A change inside this relative band is reported as "no material change" (spec
# A10: no forced attribution). The generator's noise floor moves a ~49k USD
# daily total by ~0.1%, while every injected scenario moves it by at least 3%,
# so 0.5% separates "nothing happened" from "something happened" without a
# magic absolute number - and the band is reported with the result.
MATERIALITY_RELATIVE_BAND = Decimal("0.005")


def materiality_band(baseline) -> Decimal:
    return abs(Decimal(str(baseline))) * MATERIALITY_RELATIVE_BAND


def is_material(change, baseline) -> bool:
    return abs(Decimal(str(change))) > materiality_band(baseline)


def resolve_pointer(document: dict[str, Any], pointer: str) -> Any:
    if not pointer.startswith("/"):
        raise ReportValidationError("value_pointer must be an absolute JSON pointer")
    value: Any = document
    for raw in pointer[1:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise ReportValidationError(f"value_pointer does not resolve: {pointer}")
    return value


def validate_report(report: dict[str, Any], calculations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    candidate = deepcopy(report)
    for claim in candidate.get("claims", []):
        calculation_id = str(claim.get("calculation_id", ""))
        pointer = claim.get("value_pointer")
        if not calculation_id or calculation_id not in calculations or not pointer:
            raise ReportValidationError("every claim must bind to a calculation and value pointer")
        value = resolve_pointer(calculations[calculation_id], str(pointer))
        if isinstance(value, (int, float, Decimal)) and not claim.get("query_ids"):
            raise ReportValidationError("numeric claims require query evidence")
    return candidate


def build_comparison_report(
    *,
    analysis_id: str,
    question: str,
    comparison: dict[str, Any],
    calculation_id: str,
    calculation: dict[str, Any],
    query_ids: list[str],
    dataset_ids: list[str],
    metric_key: str,
    metric_version: int,
    evidence_available_until: str | None,
    data_complete: bool = True,
    drivers: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    change = Decimal(str(calculation["change"]))
    change_pct = calculation.get("change_pct")
    material = is_material(change, calculation.get("baseline", 0))
    band = materiality_band(calculation.get("baseline", 0))
    claims: list[dict[str, Any]] = []
    limitations = ["统计分解描述相关变化，不能单独证明业务因果关系"]
    limitations.extend(str(item) for item in (warnings or []))
    calculations = {calculation_id: calculation}
    if data_complete and not material:
        limitations.append(
            f"变化 {change} 在材料性阈值 ±{band} 内，不构成需要归因的变化"
        )
    if data_complete:
        claims.append(
            {
                "id": "claim-1",
                "kind": "observed_change",
                "text": (
                    f"{metric_key} 变化 {change}"
                    + (f"（{Decimal(str(change_pct)):.2%}）" if change_pct is not None else "")
                ),
                "calculation_id": calculation_id,
                "value_pointer": "/change",
                "query_ids": query_ids,
                "dataset_ids": dataset_ids,
                "metric_versions": {metric_key: metric_version},
            }
        )
        contribution = calculation.get("contribution") or {}
        groups = list(contribution.get("groups") or [])
        if material and groups and groups[0].get("net_change_share") is not None:
            leader = groups[0]
            label = "/".join("NULL" if item is None else str(item) for item in leader.get("key", []))
            claims.append(
                {
                    "id": "claim-2",
                    "kind": "statistical_contributor",
                    "text": f"{label} 的变化贡献占净变化 {Decimal(str(leader['net_change_share'])):.2%}",
                    "calculation_id": calculation_id,
                    "value_pointer": "/contribution/groups/0/net_change_share",
                    "query_ids": query_ids,
                    "dataset_ids": dataset_ids,
                    "metric_versions": {metric_key: metric_version},
                }
            )
        if material and drivers and drivers.get("status") == "ok":
            driver_calculation_id = str(drivers["calculation_id"])
            calculations[driver_calculation_id] = drivers
            impression_effect = Decimal(str(drivers["impression_effect"]))
            ecpm_effect = Decimal(str(drivers["ecpm_effect"]))
            dominant = (
                ("impression_effect", impression_effect, "展示量")
                if abs(impression_effect) >= abs(ecpm_effect)
                else ("ecpm_effect", ecpm_effect, "eCPM")
            )
            label = "/".join("NULL" if item is None else str(item) for item in drivers.get("target_key", []))
            claims.append(
                {
                    "id": "claim-3",
                    "kind": "statistical_contributor",
                    "text": (
                        f"{label} 的收入变化中，展示量效应 {impression_effect}，"
                        f"eCPM 效应 {ecpm_effect}；主导因子为{dominant[2]}"
                    ),
                    "calculation_id": driver_calculation_id,
                    "value_pointer": f"/{dominant[0]}",
                    "query_ids": list(drivers.get("query_ids") or []),
                    "dataset_ids": dataset_ids,
                    "metric_versions": {
                        metric_key: metric_version,
                        str(drivers.get("impressions_metric") or "impressions"): int(
                            drivers.get("impressions_metric_version", metric_version)
                        ),
                    },
                }
            )
        elif material and drivers and drivers.get("status") == "undefined_impressions":
            limitations.append("目标组合在某一期间没有展示量，eCPM 未定义，未做因子拆分")
    else:
        limitations.append("数据完整性检查未通过，已阻止变化归因结论")
    hypotheses = [] if (not material or not data_complete) else [
        {"text": "需要结合贡献分解和业务配置进一步核查变化来源", "status": "unverified"}
    ]
    if material and data_complete and drivers and drivers.get("status") == "ok":
        hypotheses = [
            {"text": "可进一步核查该组合的填充率或广告配置变化", "status": "unverified"}
        ]
    report = {
        "schema_version": 1,
        "analysis_id": analysis_id,
        "status": "COMPLETED" if data_complete else "PARTIAL",
        "resolved_question": question,
        "comparison": comparison,
        "materiality": {
            "relative_band": str(MATERIALITY_RELATIVE_BAND),
            "absolute_threshold": str(band),
            "material": material,
        },
        "claims": claims,
        "hypotheses": hypotheses,
        "chart_ids": [],
        "limitations": limitations,
        "evidence_available_until": evidence_available_until,
    }
    return validate_report(report, calculations)
