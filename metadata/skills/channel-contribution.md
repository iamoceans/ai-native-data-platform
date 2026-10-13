---
name: channel-contribution
description: 渠道贡献分析：按广告网络与平台拆分收入变化，识别集中度与相互抵消的片段。
license: Apache-2.0
compatibility: 依赖 ads_revenue 指标与既有贡献分解；不做渠道成本或 LTV 分析（平台没有这些数据）。
metadata:
  askdata:
    schema_version: 1
    metric_keys: [ads_revenue]
    preferred_dimensions: [ad_network, platform, country]
    required_inputs: [metric_key, baseline_period, current_period]
---

# 渠道贡献分析

用于“哪个渠道/网络在推动变化”这类问题。

1. **优先维度**：广告网络与平台优先，其次国家。维度组合不超过平台允许的上限，超出时
   先让用户选择保留哪些。
2. **看集中度**：如果变化集中在少数片段，说明是哪几个；如果分散在很多片段，直说分散，
   不要挑一个最大的当作全部原因。
3. **注意相互抵消**：存在方向相反的片段时，明确指出它们相互抵消，净变化小于各自变化
   之和。这种情况最容易让人误判。
4. **低支撑片段要标注**：样本很小或置信不足的片段，按平台给出的低支撑标记呈现，不要
   当作可靠结论。
5. **不越界**：没有渠道成本、用户价值或预算数据时，不要评价“值不值得投放”。

所有数字只能引用已完成分析的结果，并说明它来自哪次分析。
