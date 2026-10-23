#!/usr/bin/env python
"""
BM25 检索模块 - 从 daily-paper-reader 提取并改编

提供轻量级 BM25 实现，用于 arXiv 论文检索。
不依赖外部 BM25 库，纯 Python 实现。

原作者: ziwenhahaha
改编: AI Assistant
日期: 2026-06-10
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Set, Any


TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")
MAIN_TERM_WEIGHT = 1.0
RELATED_TERM_WEIGHT = 0.5
QUERY_TEXT_WEIGHT = 0
DEFAULT_OR_SOFT_WEIGHT = 0.3


def tokenize(text: str) -> List[str]:
    """简单分词：英文按词，中文按单字。"""
    if not text:
        return []
    return TOKEN_RE.findall(text.lower())


@dataclass
class Paper:
    """用于 BM25 检索阶段的论文结构（只关心元数据和 tag）"""

    id: str
    title: str
    abstract: str
    authors: List[str] = field(default_factory=list)
    primary_category: str = ""
    categories: List[str] = field(default_factory=list)
    published: str = ""
    link: str = ""
    source: str = "arxiv"
    tags: Set[str] = field(default_factory=set)

    @property
    def text_for_bm25(self) -> str:
        """用于 BM25 的文本：标题 + 摘要（带标签，便于结构化）"""
        title = (self.title or "").strip()
        abstract = (self.abstract or "").strip()
        if title and abstract:
            return f"Title: {title}\n\nAbstract: {abstract}"
        if title:
            return f"Title: {title}"
        if abstract:
            return f"Abstract: {abstract}"
        return ""

    def to_dict(self) -> Dict[str, Any]:
        """转换为可 JSON 序列化的字典"""
        return {
            "id": self.id,
            "source": self.source,
            "title": self.title,
            "abstract": self.abstract,
            "authors": self.authors,
            "primary_category": self.primary_category,
            "categories": self.categories,
            "published": self.published,
            "link": self.link,
            "tags": sorted(self.tags),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Paper":
        """从字典创建 Paper 对象"""
        return cls(
            id=str(data.get("id") or "").strip(),
            source=str(data.get("source") or "arxiv").strip() or "arxiv",
            title=str(data.get("title") or "").strip(),
            abstract=str(data.get("abstract") or "").strip(),
            authors=[str(a) for a in (data.get("authors") or [])],
            primary_category=str(data.get("primary_category") or ""),
            categories=[str(c) for c in (data.get("categories") or [])],
            published=str(data.get("published") or ""),
            link=str(data.get("link") or ""),
        )


class BM25Index:
    """轻量 BM25 实现，避免额外依赖。"""

    def __init__(self, tokenized_docs: List[List[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b

        self.doc_len = [len(tokens) for tokens in tokenized_docs]
        self.avgdl = sum(self.doc_len) / max(len(self.doc_len), 1)
        self.doc_freqs: List[Dict[str, int]] = []
        self.idf: Dict[str, float] = {}
        self.inverted: Dict[str, List[tuple[int, int]]] = {}

        df: Dict[str, int] = {}
        for idx, tokens in enumerate(tokenized_docs):
            freqs: Dict[str, int] = {}
            for t in tokens:
                freqs[t] = freqs.get(t, 0) + 1
            self.doc_freqs.append(freqs)
            for t in freqs.keys():
                df[t] = df.get(t, 0) + 1
                self.inverted.setdefault(t, []).append((idx, freqs[t]))

        total_docs = len(tokenized_docs)
        for t, dfn in df.items():
            # 标准 BM25 IDF
            self.idf[t] = math.log(1 + (total_docs - dfn + 0.5) / (dfn + 0.5))

    def score(self, query_tokens: List[str]) -> List[float]:
        """计算查询与所有文档的 BM25 分数。"""
        scores = [0.0] * len(self.doc_len)
        if not self.doc_len:
            return scores

        q_tf: Dict[str, int] = {}
        for t in query_tokens:
            q_tf[t] = q_tf.get(t, 0) + 1

        for term, q_count in q_tf.items():
            idf = self.idf.get(term)
            if idf is None:
                continue
            postings = self.inverted.get(term, [])
            for doc_idx, tf in postings:
                dl = self.doc_len[doc_idx]
                denom = tf + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                score = idf * (tf * (self.k1 + 1) / denom) * q_count
                scores[doc_idx] += score

        return scores


def build_bm25_index(papers: List[Paper], k1: float = 1.5, b: float = 0.75) -> BM25Index:
    """为论文列表构建 BM25 索引。"""
    docs = [p.text_for_bm25 for p in papers]
    tokenized = [tokenize(d) for d in docs]
    return BM25Index(tokenized_docs=tokenized, k1=k1, b=b)


def rank_papers_by_bm25(
    bm25: BM25Index,
    papers: List[Paper],
    query: str,
    top_k: int = 50,
) -> List[tuple[int, float]]:
    """
    使用 BM25 对论文进行排序。

    Args:
        bm25: BM25 索引
        papers: 论文列表
        query: 查询文本
        top_k: 返回前 k 篇论文

    Returns:
        List[tuple[int, float]]: (论文索引, 分数) 列表，按分数降序排列
    """
    query_tokens = tokenize(query)
    scores = bm25.score(query_tokens)

    # 按分数降序排列
    indexed_scores = list(enumerate(scores))
    indexed_scores.sort(key=lambda x: x[1], reverse=True)

    # 返回前 top_k 个结果
    if top_k > 0:
        indexed_scores = indexed_scores[:top_k]

    return indexed_scores


def rank_papers_by_bm25_with_terms(
    bm25: BM25Index,
    papers: List[Paper],
    query_terms: List[Dict[str, Any]],
    top_k: int = 50,
) -> List[tuple[int, float]]:
    """
    使用带权重的查询词进行 BM25 排序。

    Args:
        bm25: BM25 索引
        papers: 论文列表
        query_terms: 查询词列表，每个元素为 {"text": "term", "weight": 1.0}
        top_k: 返回前 k 篇论文

    Returns:
        List[tuple[int, float]]: (论文索引, 分数) 列表，按分数降序排列
    """
    if not query_terms:
        return []

    # 计算每个查询词的加权分数
    all_scores = None
    total_weight = 0.0

    for term_info in query_terms:
        term_text = str(term_info.get("text") or "").strip()
        weight = float(term_info.get("weight", 1.0))

        if not term_text or weight <= 0:
            continue

        term_scores = bm25.score(tokenize(term_text))

        if all_scores is None:
            all_scores = [0.0] * len(term_scores)

        for i, s in enumerate(term_scores):
            all_scores[i] += weight * s

        total_weight += weight

    if all_scores is None:
        return []

    # 归一化
    if total_weight > 0:
        all_scores = [s / total_weight for s in all_scores]

    # 按分数降序排列
    indexed_scores = list(enumerate(all_scores))
    indexed_scores.sort(key=lambda x: x[1], reverse=True)

    # 返回前 top_k 个结果
    if top_k > 0:
        indexed_scores = indexed_scores[:top_k]

    return indexed_scores


def filter_papers_by_bm25(
    papers: List[Paper],
    indexed_scores: List[tuple[int, float]],
    min_score: float = 0.0,
) -> List[Paper]:
    """
    根据 BM25 分数过滤论文。

    Args:
        papers: 论文列表
        indexed_scores: (论文索引, 分数) 列表
        min_score: 最小分数阈值

    Returns:
        List[Paper]: 过滤后的论文列表
    """
    result = []
    for idx, score in indexed_scores:
        if score >= min_score and 0 <= idx < len(papers):
            result.append(papers[idx])
    return result


import math


def normalize_scores(scores: List[float]) -> List[float]:
    """将分数归一化到 [0, 1] 范围。"""
    if not scores:
        return scores

    min_score = min(scores)
    max_score = max(scores)

    if max_score - min_score < 1e-9:
        return [1.0] * len(scores)

    return [(s - min_score) / (max_score - min_score) for s in scores]
