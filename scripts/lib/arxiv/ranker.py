#!/usr/bin/env python
"""
Qwen3-Reranker 重排序模块 - 从 daily-paper-reader 提取并改编

提供本地和远程两种重排序方式：
1. LocalQwenReranker - 本地部署 Qwen3-Reranker-0.6B
2. SiliconFlowReranker - 远程 API（可选）

原作者: ziwenhahaha
改编: AI Assistant
日期: 2026-06-10
"""

import os
from typing import Dict, List, Any, Optional


# 默认配置
DEFAULT_LOCAL_RERANK_MODEL = "Qwen/Qwen3-Reranker-0.6B"
DEFAULT_LOCAL_RERANK_BATCH_SIZE = 8
DEFAULT_RRF_K = 60

# Rerank profile 配置
RERANK_PROFILE_CONFIGS: Dict[str, Dict[str, str]] = {
    "public-zwwen-rerank": {
        "provider": "public_zwwen",
        "model": "Qwen/Qwen3-Reranker-0.6B",
        "base_url": "https://zwwen.online/rerank",
    },
    "local-qwen3-0.6b": {
        "provider": "local",
        "model": "Qwen/Qwen3-Reranker-0.6B",
    },
    "siliconflow-qwen3-0.6b": {
        "provider": "siliconflow",
        "model": "Qwen/Qwen3-Reranker-0.6B",
        "base_url": "https://api.siliconflow.cn/v1/rerank",
    },
}


def normalize_rerank_profile(value: str) -> str:
    """归一化 rerank profile 名称。"""
    text = str(value or "").strip().lower().replace("_", "-")
    
    aliases = {
        "local": "local-qwen3-0.6b",
        "local-0.6b": "local-qwen3-0.6b",
        "qwen3-0.6b-local": "local-qwen3-0.6b",
        "siliconflow": "siliconflow-qwen3-0.6b",
        "siliconflow-0.6b": "siliconflow-qwen3-0.6b",
        "sf-0.6b": "siliconflow-qwen3-0.6b",
        "public": "public-zwwen-rerank",
        "public-rerank": "public-zwwen-rerank",
        "public-zwwen": "public-zwwen-rerank",
        "zwwen": "public-zwwen-rerank",
    }
    
    return aliases.get(text, text)


def normalize_rerank_provider(value: str) -> str:
    """归一化 rerank provider 名称。"""
    text = str(value or "").strip().lower().replace("_", "-")
    
    if text in {"", "local", "hf", "huggingface"}:
        return "local"
    if text in {"siliconflow", "sf"}:
        return "siliconflow"
    if text in {"public", "public-zwwen", "public-zwwen-rerank", "zwwen"}:
        return "public_zwwen"
    if text in {"remote"}:
        return "siliconflow"
    
    return text


def resolve_rerank_profile_config(profile: str) -> Dict[str, str]:
    """解析 rerank profile 配置。"""
    normalized = normalize_rerank_profile(profile)
    return dict(RERINK_PROFILE_CONFIGS.get(normalized) or {})


def resolve_default_rerank_model() -> str:
    """解析默认的 rerank 模型。"""
    profile_config = resolve_rerank_profile_config(os.getenv("RERANK_PROFILE", ""))
    
    if profile_config.get("model"):
        return profile_config["model"]
    
    provider = normalize_rerank_provider(os.getenv("RERANK_PROVIDER") or "public_zwwen")
    
    if provider == "local":
        return os.getenv("LOCAL_RERANK_MODEL") or os.getenv("RERANK_MODEL") or DEFAULT_LOCAL_RERANK_MODEL
    
    return os.getenv("RERANK_MODEL") or DEFAULT_LOCAL_RERANK_MODEL


class LocalQwenReranker:
    """
    本地 Qwen3 reranker，按 yes/no token 概率为 query-document 打分。
    
    依赖:
    - torch
    - transformers
    - 模型: Qwen/Qwen3-Reranker-0.6B (~1.2GB)
    """
    
    def __init__(
        self,
        model_name: str = DEFAULT_LOCAL_RERANK_MODEL,
        *,
        device: str = "",
        batch_size: int = DEFAULT_LOCAL_RERANK_BATCH_SIZE,
        max_length: int = 8192,
    ) -> None:
        """
        初始化本地 Qwen3 Reranker。
        
        Args:
            model_name: 模型名称（默认 Qwen/Qwen3-Reranker-0.6B）
            device: 设备（cpu/cuda，空则自动选择）
            batch_size: 批处理大小（默认 8）
            max_length: 最大 token 长度（默认 8192）
        """
        self.model_name = str(model_name or DEFAULT_LOCAL_RERANK_MODEL).strip()
        self.batch_size = max(int(batch_size or DEFAULT_LOCAL_RERANK_BATCH_SIZE), 1)
        self.max_length = max(int(max_length or 8192), 256)
        
        # 加载模型
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except Exception as exc:
            raise RuntimeError(
                "本地 reranker 需要 torch 与 transformers，请先安装 "
                "requirements-retrieval.txt（不是 requirements.txt）。"
            ) from exc
        
        self.torch = torch
        self.device = str(device or "").strip() or ("cuda" if torch.cuda.is_available() else "cpu")
        
        self.tokenizer = AutoTokenizer.from_pretrained(
            self.model_name,
            padding_side="left",
            trust_remote_code=True,
        )
        
        model_kwargs: Dict[str, Any] = {"trust_remote_code": True}
        
        if self.device == "cpu":
            model_kwargs["dtype"] = torch.float32
        
        try:
            self.model = AutoModelForCausalLM.from_pretrained(self.model_name, **model_kwargs)
        except TypeError:
            if "dtype" in model_kwargs:
                model_kwargs["torch_dtype"] = model_kwargs.pop("dtype")
            else:
                model_kwargs.pop("torch_dtype", None)
            self.model = AutoModelForCausalLM.from_pretrained(self.model_name, **model_kwargs)
        
        self.model.to(self.device)
        self.model.eval()
        
        # 获取 yes/no token IDs
        self.token_false_id = self.tokenizer.convert_tokens_to_ids("no")
        self.token_true_id = self.tokenizer.convert_tokens_to_ids("yes")
        
        if self.token_false_id is None or self.token_true_id is None:
            raise RuntimeError("Qwen3 reranker tokenizer 缺少 yes/no token，无法计算相关性。")
        
        # 构建前缀和后缀
        self.prefix = (
            "<|im_start|>system\n"
            "Judge whether the Document meets the requirements based on the Query and the Instruct provided. "
            "Note that the answer can only be \"yes\" or \"no\""
            "<|im_end|>\n"
            "<|im_start|>user\n"
        )
        
        self.suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
        
        self.prefix_tokens = self.tokenizer.encode(self.prefix, add_special_tokens=False)
        self.suffix_tokens = self.tokenizer.encode(self.suffix, add_special_tokens=False)
    
    @staticmethod
    def _format_pair(query: str, document: str) -> str:
        """格式化查询-文档对。"""
        instruction = "Given an academic search query, retrieve papers that best satisfy the query."
        return f"<Instruct>: {instruction}\n<Query>: {query}\n<Document>: {document}"
    
    def _score_batch(self, query: str, documents: List[str]) -> List[float]:
        """
        对一批文档进行重排序打分。
        
        Args:
            query: 查询文本
            documents: 文档列表（每个元素为 "Title: ...\nAbstract: ..."）
            
        Returns:
            List[float]: 每个文档的相关性分数（yes 概率）
        """
        # 格式化文档
        pair_texts = [self._format_pair(query, doc) for doc in documents]
        
        # 计算最大长度
        content_max_length = max(
            self.max_length - len(self.prefix_tokens) - len(self.suffix_tokens),
            32,
        )
        
        # Tokenize
        encoded = self.tokenizer(
            pair_texts,
            padding=False,
            truncation=True,
            max_length=content_max_length,
            return_attention_mask=False,
        )
        
        # 构建输入
        input_ids = [
            self.prefix_tokens + item + self.suffix_tokens
            for item in encoded.get("input_ids", [])
        ]
        
        inputs = self.tokenizer.pad(
            {"input_ids": input_ids},
            padding=True,
            return_tensors="pt",
        )
        
        inputs = {key: value.to(self.model.device) for key, value in inputs.items()}
        
        # 前向传播
        with self.torch.no_grad():
            logits = self.model(**inputs).logits[:, -1, :]
            false_scores = logits[:, self.token_false_id]
            true_scores = logits[:, self.token_true_id]
            yes_no_scores = self.torch.stack([false_scores, true_scores], dim=1)
            probs = self.torch.nn.functional.softmax(yes_no_scores, dim=1)[:, 1]
        
        return [float(score) for score in probs.detach().cpu().tolist()]
    
    def rerank(
        self,
        *,
        query: str,
        documents: List[str],
        top_n: Optional[int] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        对文档进行重排序。
        
        Args:
            query: 查询文本
            documents: 文档列表
            top_n: 返回前 n 个结果（None 表示全部）
            model: 模型名称（可选）
            
        Returns:
            Dict: {"results": [{"index": idx, "relevance_score": score}, ...], "model": model_name}
        """
        query_text = str(query or "").strip()
        
        if not query_text:
            raise ValueError("rerank: query 不能为空")
        
        if not documents:
            raise ValueError("rerank: documents 不能为空")
        
        results: List[Dict[str, Any]] = []
        
        # 批处理
        for start in range(0, len(documents), self.batch_size):
            batch_docs = [str(doc or "") for doc in documents[start : start + self.batch_size]]
            
            for offset, score in enumerate(self._score_batch(query_text, batch_docs)):
                results.append({"index": start + offset, "relevance_score": float(score)})
        
        # 按分数降序排列
        results.sort(key=lambda item: item["relevance_score"], reverse=True)
        
        # 返回前 top_n 个结果
        if top_n is not None:
            results = results[: max(int(top_n), 0)]
        
        return {"results": results, "model": model or self.model_name}


class SiliconFlowReranker:
    """
    远程 Qwen3 reranker（通过 SiliconFlow API）。
    
    依赖:
    - requests
    - API Key: SILICONFLOW_API_KEY 或 RERANK_API_KEY
    """
    
    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = "",
        model: str = DEFAULT_LOCAL_RERANK_MODEL,
    ) -> None:
        """
        初始化远程 Reranker。
        
        Args:
            api_key: API 密钥
            base_url: API 地址
            model: 模型名称
        """
        self.model = model or DEFAULT_LOCAL_RERANK_MODEL
        
        # 解析 API Key
        self.api_key = self._resolve_api_key(api_key)
        
        if not self.api_key:
            raise RuntimeError("远端 reranker 缺少 SILICONFLOW_API_KEY、PUBLIC_RERANK_API_KEY 或 RERANK_API_KEY。")
        
        # 解析 Base URL
        self.base_url = self._resolve_base_url(base_url)
        
        if not self.base_url:
            raise RuntimeError("远端 reranker 缺少 API Base URL。")
    
    @staticmethod
    def _resolve_api_key(provider: str, explicit: str = "") -> str:
        """解析 API Key。"""
        if explicit:
            return explicit
        
        if provider in {"siliconflow", "public_zwwen"}:
            return (
                os.getenv("SILICONFLOW_API_KEY")
                or os.getenv("RERANK_API_KEY")
                or os.getenv("PUBLIC_RERANK_API_KEY")
                or os.getenv("DPR_PUBLIC_SERVICE_API_KEY")
                or ""
            ).strip()
        
        return (os.getenv("RERANK_API_KEY") or "").strip()
    
    @staticmethod
    def _resolve_base_url(provider: str, profile_config: Dict[str, str], explicit: str = "") -> str:
        """解析 Base URL。"""
        if explicit:
            return explicit
        
        if provider == "siliconflow":
            return (
                os.getenv("SILICONFLOW_RERANK_URL")
                or os.getenv("RERANK_API_BASE_URL")
                or profile_config.get("base_url")
                or "https://api.siliconflow.cn/v1/rerank"
            ).strip()
        
        if provider == "public_zwwen":
            return (
                os.getenv("PUBLIC_RERANK_API_BASE_URL")
                or os.getenv("RERANK_API_BASE_URL")
                or profile_config.get("base_url")
                or "https://zwwen.online/rerank"
            ).strip()
        
        return (os.getenv("RERANK_API_BASE_URL") or profile_config.get("base_url") or "").strip()
    
    def rerank(
        self,
        *,
        query: str,
        documents: List[str],
        top_n: Optional[int] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        对文档进行重排序（通过远程 API）。
        
        Args:
            query: 查询文本
            documents: 文档列表
            top_n: 返回前 n 个结果（None 表示全部）
            model: 模型名称（可选）
            
        Returns:
            Dict: {"results": [{"index": idx, "relevance_score": score}, ...], "model": model_name}
        """
        query_text = str(query or "").strip()
        
        if not query_text:
            raise ValueError("rerank: query 不能为空")
        
        if not documents:
            raise ValueError("rerank: documents 不能为空")
        
        # TODO: 实现远程 API 调用
        # 这里需要先了解 API 的具体格式
        # 暂时返回空结果
        
        raise NotImplementedError("远程 Reranker API 调用尚未实现。请使用本地 Reranker。")


def create_ranker(
    rerank_profile: str = "local-qwen3-0.6b",
    *,
    device: str = "",
    batch_size: int = DEFAULT_LOCAL_RERANK_BATCH_SIZE,
    **kwargs,
) -> LocalQwenReranker:
    """
    创建 Reranker 实例。
    
    Args:
        rerank_profile: Rerank profile 名称
        device: 设备（cpu/cuda）
        batch_size: 批处理大小
        **kwargs: 传递给 Reranker 的其他参数
        
    Returns:
        Reranker 实例
    """
    profile_config = resolve_rerank_profile_config(rerank_profile)
    provider = normalize_rerank_provider(
        kwargs.get("provider") or profile_config.get("provider") or "local"
    )
    
    if provider == "local":
        model_name = (
            kwargs.get("model")
            or profile_config.get("model")
            or DEFAULT_LOCAL_RERANK_MODEL
        )
        
        return LocalQwenReranker(
            model_name=model_name,
            device=device,
            batch_size=batch_size,
        )
    
    elif provider in {"siliconflow", "public_zwwen"}:
        api_key = kwargs.get("api_key") or SiliconFlowReranker._resolve_api_key(provider)
        base_url = kwargs.get("base_url") or SiliconFlowReranker._resolve_base_url(provider, profile_config)
        
        return SiliconFlowReranker(
            api_key=api_key,
            base_url=base_url,
            model=kwargs.get("model") or DEFAULT_LOCAL_RERANK_MODEL,
        )
    
    else:
        raise RuntimeError(f"不支持的 reranker provider：{provider}")


def format_doc(title: str, abstract: str, max_chars: int = 850) -> str:
    """格式化文档（标题 + 摘要）。"""
    content = f"Title: {title}\nAbstract: {abstract}".strip()
    
    if len(content) > max_chars:
        content = content[:max_chars]
    
    return content


def build_documents(papers: List[Dict[str, Any]]) -> List[str]:
    """从论文列表构建文档列表。"""
    docs: List[str] = []
    
    for p in papers:
        title = (p.get("title") or "").strip()
        abstract = (p.get("abstract") or "").strip()
        
        if title or abstract:
            docs.append(format_doc(title, abstract))
        else:
            docs.append(f"[Empty paper {p.get('id', 'unknown')}]")
    
    return docs


def rerank_papers(
    ranker: LocalQwenReranker,
    query: str,
    papers: List[Dict[str, Any]],
    top_n: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    对论文进行重排序。
    
    Args:
        ranker: Reranker 实例
        query: 查询文本
        papers: 论文列表（每个元素为字典，包含 title 和 abstract）
        top_n: 返回前 n 个结果（None 表示全部）
        
    Returns:
        List[Dict]: 重排序后的论文列表，每个元素包含：
            - paper: 原始论文字典
            - relevance_score: 相关性分数
            - rank: 排名
    """
    if not papers:
        return []
    
    # 构建文档
    documents = build_documents(papers)
    
    # 重排序
    result = ranker.rerank(
        query=query,
        documents=documents,
        top_n=len(documents),
    )
    
    # 格式化结果
    ranked = []
    
    for rank_idx, item in enumerate(result.get("results", []), start=1):
        idx = int(item.get("index", -1))
        score = float(item.get("relevance_score", 0.0))
        
        if idx < 0 or idx >= len(papers):
            continue
        
        ranked.append({
            "paper": papers[idx],
            "relevance_score": score,
            "rank": rank_idx,
        })
    
    # 返回前 top_n 个结果
    if top_n is not None:
        ranked = ranked[: max(int(top_n), 0)]
    
    return ranked


class Ranker:
    """TF-IDF ranker (wrapper around retriever.py).
    
    Ranks papers against the library (uses BM25 + Embedding + RRF).
    """
    
    def __init__(self, use_embedding: bool = False) -> None:
        """
        Initialize Ranker.
        
        Args:
            use_embedding: Whether to use embedding retrieval (default: False)
        """
        self.use_embedding = use_embedding
    
    def Rank(self, papers: list, library_abstracts: dict) -> list:
        """
        Rank papers against library abstracts.
        
        Args:
            papers: List of Paper objects to rank
            library_abstracts: Dict of {title: abstract} from library
            
        Returns:
            Ranked list of Paper objects (same list, modified in-place)
        """
        if not papers or not library_abstracts:
            return papers
        
        # Use retriever.py for ranking
        from .retriever import retrieve_papers
        
        # Build query from papers
        query = " ".join([
            p.title + " " + p.abstract
            for p in papers[:5]  # Use first 5 papers as query
        ])[:500]  # Limit query length
        
        # Retrieve and rank
        results = retrieve_papers(
            query=query,
            papers=papers,
            retrieval_mode="hybrid" if self.use_embedding else "bm25",
            top_k=len(papers),
        )
        
        # Update scores
        # results 格式: [{"rank": 1, "relevance_score": 0.95, ...}, ...]
        score_map = {}  # idx -> score
        for r in results:
            if "rank" in r and "relevance_score" in r:
                idx = r["rank"] - 1  # rank is 1-based
                if 0 <= idx < len(papers):
                    score_map[idx] = r["relevance_score"]
        
        # 如果 results 没有 rank，尝试按 order 或 index
        if not score_map:
            for i, r in enumerate(results):
                idx_key = r.get("index", r.get("order", i))
                if isinstance(idx_key, int) and 0 <= idx_key < len(papers):
                    score_map[idx_key] = r.get("relevance_score", r.get("score", 0.0))
        
        # 应用分数
        for idx, score in score_map.items():
            if idx < len(papers):
                papers[idx].score = score
        
        return papers
