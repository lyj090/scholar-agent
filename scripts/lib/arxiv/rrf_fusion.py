#!/usr/bin/env python
"""
RRF (Reciprocal Rank Fusion) 结果融合模块

将 BM25 和 Embedding 等多路检索结果进行融合，
使用 RRF 算法生成最终的排序结果。

公式:
    RRF_score(d) = Σ (1 / (k + rank_i(d)))
    
其中:
    - k: 常数（默认 60）
    - rank_i(d): 论文 d 在第 i 路结果中的排名（从 1 开始）

原作者: ziwenhahaha
改编: AI Assistant
日期: 2026-06-10
"""

from typing import List, Dict, Tuple, Any


# 默认 RRF 常数
DEFAULT_RRF_K = 60


def merge_by_rrf(
    results_list: List[List[Tuple[int, float]]],
    k: int = DEFAULT_RRF_K,
    top_n: int = 0,
) -> List[Tuple[int, float]]:
    """
    使用 RRF 算法融合多路检索结果。
    
    Args:
        results_list: 多路检索结果，每路为 [(论文索引, 分数), ...] 列表
        k: RRF 常数（默认 60）
        top_n: 返回前 n 个结果（0 表示返回全部）
        
    Returns:
        List[Tuple[int, float]]: (论文索引, RRF 分数) 列表，按分数降序排列
    """
    if not results_list:
        return []
    
    # 计算每篇论文的 RRF 分数
    rrf_scores: Dict[int, float] = {}
    hit_count: Dict[int, int] = {}
    
    for results in results_list:
        for rank, (idx, _score) in enumerate(results, start=1):
            if idx < 0:
                continue
            
            # RRF 分数 = 1 / (k + rank)
            rrf_score = 1.0 / (k + rank)
            
            rrf_scores[idx] = rrf_scores.get(idx, 0.0) + rrf_score
            hit_count[idx] = hit_count.get(idx, 0) + 1
    
    # 按 RRF 分数降序排列
    sorted_items = sorted(
        rrf_scores.items(),
        key=lambda x: (-x[1], -hit_count.get(x[0], 0), x[0])
    )
    
    # 返回前 top_n 个结果
    if top_n > 0:
        sorted_items = sorted_items[:top_n]
    
    return sorted_items


def merge_by_rrf_with_weights(
    results_list: List[Tuple[List[Tuple[int, float]], float]],
    k: int = DEFAULT_RRF_K,
    top_n: int = 0,
) -> List[Tuple[int, float]]:
    """
    使用带权重的 RRF 算法融合多路检索结果。
    
    Args:
        results_list: 多路检索结果，每路为 ([(论文索引, 分数), ...], weight) 元组
        k: RRF 常数（默认 60）
        top_n: 返回前 n 个结果（0 表示返回全部）
        
    Returns:
        List[Tuple[int, float]]: (论文索引, RRF 分数) 列表，按分数降序排列
    """
    if not results_list:
        return []
    
    # 计算每篇论文的加权 RRF 分数
    rrf_scores: Dict[int, float] = {}
    hit_count: Dict[int, int] = {}
    
    for results, weight in results_list:
        if not results or weight <= 0:
            continue
        
        for rank, (idx, _score) in enumerate(results, start=1):
            if idx < 0:
                continue
            
            # 加权 RRF 分数 = weight * (1 / (k + rank))
            rrf_score = weight * (1.0 / (k + rank))
            
            rrf_scores[idx] = rrf_scores.get(idx, 0.0) + rrf_score
            hit_count[idx] = hit_count.get(idx, 0) + 1
    
    # 按 RRF 分数降序排列
    sorted_items = sorted(
        rrf_scores.items(),
        key=lambda x: (-x[1], -hit_count.get(x[0], 0), x[0])
    )
    
    # 返回前 top_n 个结果
    if top_n > 0:
        sorted_items = sorted_items[:top_n]
    
    return sorted_items


def normalize_scores(scores: List[float]) -> List[float]:
    """将分数归一化到 [0, 1] 范围。"""
    if not scores:
        return scores
    
    min_score = min(scores)
    max_score = max(scores)
    
    if max_score - min_score < 1e-9:
        return [1.0] * len(scores)
    
    return [(s - min_score) / (max_score - min_score) for s in scores]


def normalize_rrf_results(
    rrf_results: List[Tuple[int, float]]
) -> List[Tuple[int, float]]:
    """
    将 RRF 结果归一化到 [0, 1] 范围。
    
    Args:
        rrf_results: (论文索引, RRF 分数) 列表
        
    Returns:
        归一化后的 (论文索引, 归一化分数) 列表
    """
    if not rrf_results:
        return []
    
    scores = [score for _, score in rrf_results]
    normalized = normalize_scores(scores)
    
    return [(idx, norm_score) for (idx, _), norm_score in zip(rrf_results, normalized)]


def hybrid_retrieval(
    bm25_results: List[Tuple[int, float]],
    embedding_results: List[Tuple[int, float]],
    bm25_weight: float = 0.5,
    embedding_weight: float = 0.5,
    k: int = DEFAULT_RRF_K,
    top_n: int = 0,
) -> List[Tuple[int, float]]:
    """
    混合检索：使用 RRF 融合 BM25 和 Embedding 结果。
    
    Args:
        bm25_results: BM25 检索结果 [(论文索引, BM25 分数), ...]
        embedding_results: Embedding 检索结果 [(论文索引, 相似度), ...]
        bm25_weight: BM25 结果权重（默认 0.5）
        embedding_weight: Embedding 结果权重（默认 0.5）
        k: RRF 常数（默认 60）
        top_n: 返回前 n 个结果（0 表示返回全部）
        
    Returns:
        List[Tuple[int, float]]: (论文索引, RRF 分数) 列表，按分数降序排列
    """
    results_list = [
        (bm25_results, bm25_weight),
        (embedding_results, embedding_weight),
    ]
    
    return merge_by_rrf_with_weights(results_list, k=k, top_n=top_n)


def format_hybrid_results(
    rrf_results: List[Tuple[int, float]],
    papers: List[Any],
    include_scores: bool = True,
) -> List[Dict[str, Any]]:
    """
    将 RRF 结果格式化为输出格式。
    
    Args:
        rrf_results: (论文索引, RRF 分数) 列表
        papers: 论文列表
        include_scores: 是否包含分数
        
    Returns:
        List[Dict]: 格式化的结果列表
    """
    formatted = []
    
    for rank, (idx, score) in enumerate(rrf_results, start=1):
        if idx < 0 or idx >= len(papers):
            continue
        
        paper = papers[idx]
        
        # 获取论文 ID（兼容 Paper 对象或字典）
        if isinstance(paper, dict):
            paper_id = paper.get("id", "")
            title = paper.get("title", "")
            abstract = paper.get("abstract", "")
        else:
            paper_id = getattr(paper, "id", "")
            title = getattr(paper, "title", "")
            abstract = getattr(paper, "abstract", "")
        
        entry = {
            "rank": rank,
            "paper_id": paper_id,
            "title": title,
            "abstract": abstract,
        }
        
        if include_scores:
            entry["rrf_score"] = score
        
        formatted.append(entry)
    
    return formatted
