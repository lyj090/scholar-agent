#!/usr/bin/env python
"""
Embedding 检索模块 - 从 daily-paper-reader 提取并改编

使用 sentence-transformers 将论文标题+摘要编码为向量，
然后计算查询与论文的相似度，进行语义检索。

依赖:
- sentence-transformers
- torch
- numpy

原作者: ziwenhahaha
改编: AI Assistant
日期: 2026-06-10
"""

import hashlib
from typing import List, Dict, Any, Optional, Tuple
import numpy as np


# E5 模型需要的查询前缀
E5_QUERY_PREFIX = "query: "
E5_PASSAGE_PREFIX = "passage: "

# Embedding 缓存版本
EMBEDDING_CACHE_VERSION = 1


def get_device(device: str = "") -> str:
    """获取计算设备。"""
    if device:
        return device
    
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    
    return "cpu"


def load_embedding_model(
    model_name: str = "BAAI/bge-small-en-v1.5",
    device: str = "",
    **kwargs
) -> Tuple[Any, Any]:
    """
    加载 sentence-transformers 模型。
    
    Args:
        model_name: 模型名称（默认 BAAI/bge-small-en-v1.5）
        device: 设备（cpu/cuda，空则自动选择）
        **kwargs: 传递给 SentenceTransformer 的参数
        
    Returns:
        (model, tokenizer) 元组
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        raise ImportError(
            "Embedding 检索需要 sentence-transformers 库。"
            "请运行: pip install sentence-transformers"
        )
    
    device = get_device(device)
    
    model = SentenceTransformer(model_name, device=device, **kwargs)
    
    return model, None


def encode_texts(
    model,
    texts: List[str],
    batch_size: int = 8,
    max_length: int = 512,
    show_progress_bar: bool = True,
) -> np.ndarray:
    """
    将文本编码为向量。
    
    Args:
        model: SentenceTransformer 模型
        texts: 文本列表
        batch_size: 批处理大小
        max_length: 最大 token 长度（仅部分模型支持）
        show_progress_bar: 是否显示进度条
        
    Returns:
        numpy 数组，形状为 (len(texts), embedding_dim)
    """
    if not texts:
        return np.array([], dtype=np.float32)
    
    # 尝试传递 max_length，如果不支持则忽略
    try:
        embeddings = model.encode(
            texts,
            batch_size=batch_size,
            max_length=max_length,
            show_progress_bar=show_progress_bar,
            normalize_embeddings=True,  # L2 归一化
        )
    except ValueError:
        # 模型不支持 max_length，去掉该参数
        embeddings = model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=show_progress_bar,
            normalize_embeddings=True,
        )
    
    return embeddings.astype(np.float32)


def build_paper_text(paper: "Paper", use_prefix: bool = True) -> str:
    """构建用于 embedding 的论文文本。"""
    title = (paper.title or "").strip()
    abstract = (paper.abstract or "").strip()
    
    if title and abstract:
        text = f"Title: {title}\n\nAbstract: {abstract}"
    elif title:
        text = f"Title: {title}"
    elif abstract:
        text = f"Abstract: {abstract}"
    else:
        return ""
    
    if use_prefix:
        return f"{E5_PASSAGE_PREFIX}{text}"
    return text


def build_paper_texts(papers: List["Paper"], use_prefix: bool = True) -> List[str]:
    """批量构建论文文本。"""
    return [build_paper_text(p, use_prefix) for p in papers]


def build_query_text(query: str, use_prefix: bool = True) -> str:
    """构建用于 embedding 的查询文本。"""
    text = (query or "").strip()
    if not text:
        return ""
    
    if use_prefix:
        return f"{E5_QUERY_PREFIX}{text}"
    return text


def compute_similarity(
    query_embedding: np.ndarray,
    paper_embeddings: np.ndarray,
) -> np.ndarray:
    """
    计算查询与所有论文的相似度（点积）。
    
    Args:
        query_embedding: 查询向量，形状为 (D,)
        paper_embeddings: 论文向量，形状为 (N, D)
        
    Returns:
        相似度数组，形状为 (N,)
    """
    # 确保是二维数组
    if query_embedding.ndim == 1:
        query_embedding = query_embedding.reshape(1, -1)
    
    # 点积 = 余弦相似度（因为已归一化）
    similarities = np.dot(paper_embeddings, query_embedding.T).flatten()
    
    return similarities.astype(np.float32)


def rank_papers_by_embedding(
    query: str,
    papers: List["Paper"],
    paper_embeddings: np.ndarray,
    model,
    batch_size: int = 8,
    max_length: int = 512,
    top_k: int = 50,
    use_prefix: bool = True,
) -> List[Tuple[int, float]]:
    """
    使用 Embedding 对论文进行排序。
    
    Args:
        query: 查询文本
        papers: 论文列表
        paper_embeddings: 论文向量，形状为 (N, D)
        model: SentenceTransformer 模型
        batch_size: 批处理大小
        max_length: 最大 token 长度
        top_k: 返回前 k 篇论文
        use_prefix: 是否使用 E5 前缀
        
    Returns:
        List[Tuple[int, float]]: (论文索引, 相似度) 列表，按相似度降序排列
    """
    if not papers or paper_embeddings.size == 0:
        return []
    
    # 编码查询
    query_text = build_query_text(query, use_prefix)
    query_embedding = encode_texts(
        model, [query_text], batch_size=1, max_length=max_length, show_progress_bar=False
    )[0]
    
    # 计算相似度
    similarities = compute_similarity(query_embedding, paper_embeddings)
    
    # 按相似度降序排列
    indexed_scores = list(enumerate(similarities))
    indexed_scores.sort(key=lambda x: x[1], reverse=True)
    
    # 返回前 top_k 个结果
    if top_k > 0:
        indexed_scores = indexed_scores[:top_k]
    
    return indexed_scores


def build_embedding_index(
    papers: List["Paper"],
    model,
    batch_size: int = 8,
    max_length: int = 512,
    use_prefix: bool = True,
) -> np.ndarray:
    """
    为论文列表构建 Embedding 索引。
    
    Args:
        papers: 论文列表
        model: SentenceTransformer 模型
        batch_size: 批处理大小
        max_length: 最大 token 长度
        use_prefix: 是否使用 E5 前缀
        
    Returns:
        numpy 数组，形状为 (N, D)
    """
    if not papers:
        return np.array([], dtype=np.float32)
    
    # 构建文本
    texts = build_paper_texts(papers, use_prefix)
    
    # 编码
    embeddings = encode_texts(
        model, texts, batch_size=batch_size, max_length=max_length
    )
    
    return embeddings


def parse_embedding_value(value: Any) -> Optional[np.ndarray]:
    """解析 embedding 值（从 JSON 加载）。"""
    if isinstance(value, np.ndarray):
        vec = value.astype(np.float32)
    elif isinstance(value, list):
        try:
            vec = np.array([float(x) for x in value], dtype=np.float32)
        except Exception:
            return None
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1]
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if not parts:
            return None
        try:
            vec = np.array([float(p) for p in parts], dtype=np.float32)
        except Exception:
            return None
    else:
        return None
    
    if vec.ndim != 1 or vec.shape[0] <= 0:
        return None
    
    # 归一化
    norm = float(np.linalg.norm(vec))
    if norm <= 0:
        return None
    
    return vec / norm


def normalize_scores(scores: List[float]) -> List[float]:
    """将分数归一化到 [0, 1] 范围。"""
    if not scores:
        return scores
    
    min_score = min(scores)
    max_score = max(scores)
    
    if max_score - min_score < 1e-9:
        return [1.0] * len(scores)
    
    return [(s - min_score) / (max_score - min_score) for s in scores]


def filter_papers_by_embedding(
    papers: List["Paper"],
    indexed_scores: List[Tuple[int, float]],
    min_score: float = 0.0,
) -> List["Paper"]:
    """
    根据 Embedding 相似度过滤论文。
    
    Args:
        papers: 论文列表
        indexed_scores: (论文索引, 相似度) 列表
        min_score: 最小相似度阈值
        
    Returns:
        List[Paper]: 过滤后的论文列表
    """
    result = []
    for idx, score in indexed_scores:
        if score >= min_score and 0 <= idx < len(papers):
            result.append(papers[idx])
    return result


# 为了避免循环引用，这里定义一个简单的 Paper 接口
# 实际使用时应该导入 retrieval_bm25.py 中的 Paper 类

def create_paper_from_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """从字典创建 Paper 数据（简化版，返回字典）。"""
    return {
        "id": str(data.get("id") or "").strip(),
        "source": str(data.get("source") or "arxiv").strip() or "arxiv",
        "title": str(data.get("title") or "").strip(),
        "abstract": str(data.get("abstract") or "").strip(),
        "authors": [str(a) for a in (data.get("authors") or [])],
        "primary_category": str(data.get("primary_category") or ""),
        "categories": [str(c) for c in (data.get("categories") or [])],
        "published": str(data.get("published") or ""),
        "link": str(data.get("link") or ""),
        "embedding": parse_embedding_value(data.get("embedding")),
        "tags": set(data.get("tags") or []),
    }


def build_embedding_cache_key(model_name: str, text: str) -> str:
    """构建 embedding 缓存键。"""
    payload = f"v{EMBEDDING_CACHE_VERSION}|{model_name.lower()}|{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
