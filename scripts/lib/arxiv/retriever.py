#!/usr/bin/env python
"""
统一检索模块 - 整合 BM25、Embedding 和 RRF 融合

提供统一的检索接口，支持：
1. BM25 检索
2. Embedding 语义检索
3. Hybrid 检索（RRF 融合）

原作者: ziwenhahaha
改编: AI Assistant
日期: 2026-06-10
"""

import os
import sys
from typing import List, Dict, Any, Optional, Tuple

# 确保 lib/arxiv/ 可导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from retrieval_bm25 import build_bm25_index, rank_papers_by_bm25
# Import through the package so retriever shares ONE module instance with the
# CLI. Importing the bare `cache` module duplicates it, giving QUIET two
# independent copies -- --quiet would then fail silently for these lines.
from arxiv.cache import note  # noqa: E402  (needs the sys.path insert above)
# Embedding retrieval is optional: it needs torch, sentence-transformers and
# numpy, which `requirements-retrieval.txt` pins but the base install does not.
# Importing it at module scope made every arXiv command fail on a machine that
# never asked for semantic search — including `find`, which only needs BM25.
# The import is deferred to first use so the failure is a clear message on the
# one feature that needs it, not a traceback on all of them.
# Optional[...] rather than `X | None`: this module has no
# `from __future__ import annotations`, so a module-level union is evaluated at
# import time and crashes on 3.9 and earlier.
_EMBEDDING_IMPORT_ERROR: Optional[Exception] = None
try:
    from retrieval_embedding import (
        load_embedding_model,
        build_embedding_index,
        rank_papers_by_embedding,
    )
except ImportError as _exc:  # pragma: no cover - exercised via the CLI
    _EMBEDDING_IMPORT_ERROR = _exc
    load_embedding_model = build_embedding_index = rank_papers_by_embedding = None


def _require_embedding_support() -> None:
    """Raise a message a user can act on, instead of an ImportError traceback."""
    if _EMBEDDING_IMPORT_ERROR is None:
        return
    raise RuntimeError(
        "Embedding retrieval is not installed "
        f"({_EMBEDDING_IMPORT_ERROR}).\n"
        "  BM25 retrieval works without it.\n"
        "  To enable semantic search: pip install -r requirements-retrieval.txt"
    )
from rrf_fusion import (
    merge_by_rrf_with_weights,
    format_hybrid_results,
)


# 默认参数
DEFAULT_TOP_K = 50
DEFAULT_RRF_K = 60
DEFAULT_BM25_WEIGHT = 0.5
DEFAULT_EMBEDDING_WEIGHT = 0.5
DEFAULT_DEVICE = "cpu"
DEFAULT_MODEL_NAME = "BAAI/bge-small-en-v1.5"


class PaperRetriever:
    """统一检索器，支持多种检索模式。"""
    
    def __init__(
        self,
        retrieval_mode: str = "hybrid",
        model_name: str = DEFAULT_MODEL_NAME,
        device: str = "",
        bm25_weight: float = DEFAULT_BM25_WEIGHT,
        embedding_weight: float = DEFAULT_EMBEDDING_WEIGHT,
        rrf_k: int = DEFAULT_RRF_K,
        batch_size: int = 8,
        max_length: int = 512,
    ):
        """
        初始化检索器。
        
        Args:
            retrieval_mode: 检索模式 (bm25/embedding/hybrid)
            model_name: Embedding 模型名称
            device: 设备 (cpu/cuda)
            bm25_weight: BM25 结果权重（hybrid 模式）
            embedding_weight: Embedding 结果权重（hybrid 模式）
            rrf_k: RRF 常数
            batch_size: 批处理大小
            max_length: 最大 token 长度
        """
        self.retrieval_mode = retrieval_mode
        self.model_name = model_name
        self.device = device or DEFAULT_DEVICE
        self.bm25_weight = bm25_weight
        self.embedding_weight = embedding_weight
        self.rrf_k = rrf_k
        self.batch_size = batch_size
        self.max_length = max_length
        
        # 延迟加载
        self._bm25_index = None
        self._embedding_model = None
        self._paper_embeddings = None
        self._papers = None
    
    def _get_embedding_model(self):
        """获取 Embedding 模型（延迟加载）。"""
        if self._embedding_model is None:
            _require_embedding_support()
            note(f"[Retriever] 加载 Embedding 模型: {self.model_name}")
            self._embedding_model, _ = load_embedding_model(
                model_name=self.model_name,
                device=self.device,
            )
        return self._embedding_model
    
    @staticmethod
    def _normalize_papers(papers: List[Any]) -> List[Any]:
        """
        统一论文格式为 Paper 对象。
        
        Args:
            papers: 论文列表（Paper 对象或字典）
            
        Returns:
            统一的 Paper 对象列表
        """
        if not papers:
            return []
        
        # 检查第一个元素是否为字典
        if isinstance(papers[0], dict):
            # 转换为 Paper 对象
            from retrieval_bm25 import Paper
            return [Paper.from_dict(p) for p in papers]
        
        # 已经是 Paper 对象
        return papers
    
    def build_index(self, papers: List[Any]) -> None:
        """
        为论文列表构建检索索引。
        
        Args:
            papers: 论文列表（Paper 对象或字典）
        """
        # 统一格式为 Paper 对象
        self._papers = self._normalize_papers(papers)
        
        # 构建 BM25 索引
        if self.retrieval_mode in ("bm25", "hybrid"):
            note("[Retriever] 构建 BM25 索引...")
            self._bm25_index = build_bm25_index(self._papers)
            note(f"[Retriever] BM25 索引构建完成: {len(self._papers)} 篇论文")
        
        # 构建 Embedding 索引
        if self.retrieval_mode in ("embedding", "hybrid"):
            note("[Retriever] 构建 Embedding 索引...")
            model = self._get_embedding_model()
            self._paper_embeddings = build_embedding_index(
                self._papers,
                model,
                batch_size=self.batch_size,
                max_length=self.max_length,
            )
            note(f"[Retriever] Embedding 索引构建完成: {len(self._papers)} 篇论文")
    
    def retrieve(self, query: str, top_k: int = DEFAULT_TOP_K) -> List[Tuple[int, float]]:
        """
        检索相关论文。
        
        Args:
            query: 查询文本
            top_k: 返回前 k 篇论文
            
        Returns:
            List[Tuple[int, float]]: (论文索引, 分数) 列表，按分数降序排列
        """
        if not self._papers:
            raise RuntimeError("请先调用 build_index() 构建索引")
        
        if self.retrieval_mode == "bm25":
            return self._retrieve_bm25(query, top_k)
        elif self.retrieval_mode == "embedding":
            return self._retrieve_embedding(query, top_k)
        elif self.retrieval_mode == "hybrid":
            return self._retrieve_hybrid(query, top_k)
        else:
            raise ValueError(f"不支持的检索模式: {self.retrieval_mode}")
    
    def _retrieve_bm25(self, query: str, top_k: int) -> List[Tuple[int, float]]:
        """BM25 检索。"""
        return rank_papers_by_bm25(
            self._bm25_index,
            self._papers,
            query,
            top_k=top_k,
        )
    
    def _retrieve_embedding(self, query: str, top_k: int) -> List[Tuple[int, float]]:
        """Embedding 检索。"""
        _require_embedding_support()
        model = self._get_embedding_model()
        return rank_papers_by_embedding(
            query,
            self._papers,
            self._paper_embeddings,
            model,
            batch_size=self.batch_size,
            max_length=self.max_length,
            top_k=top_k,
        )
    
    def _retrieve_hybrid(self, query: str, top_k: int) -> List[Tuple[int, float]]:
        """Hybrid 检索（RRF 融合）。"""
        # BM25 检索
        bm25_results = self._retrieve_bm25(query, top_k * 2)
        
        # Embedding 检索
        embedding_results = self._retrieve_embedding(query, top_k * 2)
        
        # RRF 融合
        results_list = [
            (bm25_results, self.bm25_weight),
            (embedding_results, self.embedding_weight),
        ]
        
        return merge_by_rrf_with_weights(
            results_list,
            k=self.rrf_k,
            top_n=top_k,
        )
    
    def retrieve_and_format(
        self,
        query: str,
        top_k: int = DEFAULT_TOP_K,
        include_scores: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        检索并格式化结果。
        
        Args:
            query: 查询文本
            top_k: 返回前 k 篇论文
            include_scores: 是否包含分数
            
        Returns:
            List[Dict]: 格式化的结果列表
        """
        results = self.retrieve(query, top_k)
        
        if self.retrieval_mode == "hybrid":
            # Hybrid 模式返回的是 RRF 分数
            return format_hybrid_results(
                results,
                self._papers,
                include_scores=include_scores,
            )
        else:
            # BM25 或 Embedding 模式
            formatted = []
            for rank, (idx, score) in enumerate(results, start=1):
                if idx < 0 or idx >= len(self._papers):
                    continue
                
                paper = self._papers[idx]
                
                # 获取论文 ID 和标题（兼容 Paper 对象或字典）
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
                    entry["score"] = score
                
                formatted.append(entry)
            
            return formatted


def create_retriever(
    retrieval_mode: str = "hybrid",
    model_name: str = DEFAULT_MODEL_NAME,
    device: str = "",
    **kwargs
) -> PaperRetriever:
    """
    创建检索器。
    
    Args:
        retrieval_mode: 检索模式 (bm25/embedding/hybrid)
        model_name: Embedding 模型名称
        device: 设备 (cpu/cuda)
        **kwargs: 传递给 PaperRetriever 的其他参数
        
    Returns:
        PaperRetriever 实例
    """
    return PaperRetriever(
        retrieval_mode=retrieval_mode,
        model_name=model_name,
        device=device,
        **kwargs
    )


def retrieve_papers(
    papers: List[Any],
    query: str,
    retrieval_mode: str = "hybrid",
    model_name: str = DEFAULT_MODEL_NAME,
    device: str = "",
    top_k: int = DEFAULT_TOP_K,
    **kwargs
) -> List[Dict[str, Any]]:
    """
    一站式检索函数：构建索引并检索。
    
    Args:
        papers: 论文列表
        query: 查询文本
        retrieval_mode: 检索模式 (bm25/embedding/hybrid)
        model_name: Embedding 模型名称
        device: 设备 (cpu/cuda)
        top_k: 返回前 k 篇论文
        **kwargs: 传递给 PaperRetriever 的其他参数
        
    Returns:
        List[Dict]: 格式化的结果列表
    """
    retriever = create_retriever(
        retrieval_mode=retrieval_mode,
        model_name=model_name,
        device=device,
        **kwargs
    )
    
    retriever.build_index(papers)
    return retriever.retrieve_and_format(query, top_k=top_k)


# 命令行接口
def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="论文检索工具 - 支持 BM25、Embedding 和 Hybrid 检索"
    )
    
    parser.add_argument(
        "--mode",
        type=str,
        choices=["bm25", "embedding", "hybrid"],
        default="hybrid",
        help="检索模式（默认: hybrid）"
    )
    
    parser.add_argument(
        "--input",
        type=str,
        required=True,
        help="输入 JSON 文件（论文列表）"
    )
    
    parser.add_argument(
        "--query",
        type=str,
        required=True,
        help="查询文本"
    )
    
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help=f"返回前 k 篇论文（默认: {DEFAULT_TOP_K}）"
    )
    
    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL_NAME,
        help=f"Embedding 模型名称（默认: {DEFAULT_MODEL_NAME}）"
    )
    
    parser.add_argument(
        "--device",
        type=str,
        default="",
        help="设备（cpu/cuda，空则自动选择）"
    )
    
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="输出 JSON 文件路径（不指定则输出到 stdout）"
    )
    
    args = parser.parse_args()
    
    # 加载论文
    import json
    with open(args.input, "r", encoding="utf-8") as f:
        papers_data = json.load(f)
    
    # 转换为 Paper 对象
    from retrieval_bm25 import Paper
    
    papers = [Paper.from_dict(p) for p in papers_data]
    
    # 检索
    results = retrieve_papers(
        papers,
        query=args.query,
        retrieval_mode=args.mode,
        model_name=args.model,
        device=args.device,
        top_k=args.top_k,
    )
    
    # 输出
    output_data = {
        "query": args.query,
        "retrieval_mode": args.mode,
        "total_results": len(results),
        "results": results,
    }
    
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        note(f"[Retriever] 结果已保存到: {args.output}")
    else:
        print(json.dumps(output_data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
