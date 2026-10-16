"""Offline domain semantic vectors (v1), with bilingual concept expansion.

No network or model credential is required. Unknown vocabulary falls back to
word/character features. This is bounded-domain retrieval, not a general language
embedding model. Permissions are filtered before any vector is compared.
"""
from collections import Counter
import math
import re

CONCEPTS = {
    "revenue": ("revenue", "income", "earnings", "收入", "营收", "收益"),
    "decline": ("decline", "drop", "decrease", "fall", "下降", "下滑", "减少"),
    "increase": ("increase", "growth", "rise", "增长", "上涨", "上升"),
    "country": ("country", "region", "geography", "国家", "地区", "地域"),
    "platform": ("platform", "operating system", "os", "平台", "操作系统"),
    "app_version": ("app_version", "release", "version", "版本", "发布"),
    "ad_network": ("ad_network", "network", "广告网络", "广告渠道"),
    "impressions": ("impressions", "exposure", "展示量", "曝光"),
    "ecpm": ("ecpm", "unit price", "单价", "千次展示收入"),
    "concentration": ("concentrates", "concentration", "dominant", "主要", "集中"),
}

def embed(text: str) -> Counter:
    lowered = text.casefold()
    vector = Counter()
    for concept, synonyms in CONCEPTS.items():
        if any((term in lowered if not term.isascii() else
                re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", lowered))
               for term in synonyms):
            vector["concept:" + concept] = 4
    for word in re.findall(r"[a-z0-9_]+", lowered):
        vector["word:" + word] += 1
    for run in re.findall(r"[\u4e00-\u9fff]+", lowered):
        for index in range(len(run)-1):
            vector["char:" + run[index:index+2]] += 1
    return vector


def similarity(left: Counter, right: Counter) -> float:
    scale = math.sqrt(sum(v*v for v in left.values()) * sum(v*v for v in right.values()))
    return sum(v * right.get(k, 0) for k, v in left.items()) / scale if scale else 0.0


def rank_memories(rows, question: str):
    query = embed(question)
    if not query:
        return list(rows)
    return sorted(rows, key=lambda row: similarity(query, embed(
        row.statement + " " + " ".join(row.scope_dimensions or []))), reverse=True)
