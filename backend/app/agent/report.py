"""Evidence-bound report construction and validation."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any


class ReportValidationError(ValueError):
    pass


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
) -> dict[str, Any]:
    change = Decimal(str(calculation["change"]))
    change_pct = calculation.get("change_pct")
    material = change != 0
    claims: list[dict[str, Any]] = []
    limitations = ["统计分解描述相关变化，不能单独证明业务因果关系"]
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
    else:
        limitations.append("数据完整性检查未通过，已阻止变化归因结论")
    hypotheses = [] if (not material or not data_complete) else [
        {"text": "需要结合贡献分解和业务配置进一步核查变化来源", "status": "unverified"}
    ]
    report = {
        "schema_version": 1,
        "analysis_id": analysis_id,
        "status": "COMPLETED" if data_complete else "PARTIAL",
        "resolved_question": question,
        "comparison": comparison,
        "claims": claims,
        "hypotheses": hypotheses,
        "chart_ids": [],
        "limitations": limitations,
        "evidence_available_until": evidence_available_until,
    }
    return validate_report(report, {calculation_id: calculation})
