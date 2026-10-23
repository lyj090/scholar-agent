#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM Refiner — 使用 LLM 对候选论文进行精细化筛选。

支持两种模式：
1. API 模式：调用外部 LLM API（DeepSeek/GPT/Claude）
2. Heuristic 模式：使用启发式算法（本地、免费、可测试）
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import List, Dict, Any

from .cache import note

# 延迟导入，避免循环依赖
# Paper 在 _ParseAgentResult() 中使用


class LLMRefinerBase:
    """LLM Refiner 基类。"""
    
    def Refine(
        self,
        papers: List[Any],  # List[Paper]
        query: str,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        对候选论文进行精细化筛选。
        
        Args:
            papers: 候选论文列表
            query: 用户查询（用于评分标准）
            **kwargs: 其他参数
            
        Returns:
            [{"paper": Paper, "score": 8.5, "summary": "..."}, ...]
        """
        raise NotImplementedError


class HeuristicLLMRefiner(LLMRefinerBase):
    """Heuristic 模式：使用启发式算法（本地、免费）。
    
    适合测试和不具备 API Key 的场景。
    """
    
    def __init__(self, use_keywords: bool = True) -> None:
        """
        初始化 Heuristic Refiner。
        
        Args:
            use_keywords: 是否使用关键词匹配（默认 True）
        """
        self.use_keywords = use_keywords
    
    def Refine(
        self,
        papers: List[Any],
        query: str,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """使用启发式算法对论文进行精细化筛选。"""
        refined = []
        query_lower = query.lower()
        query_keywords = set(re.findall(r'\w+', query_lower))
        
        for i, paper in enumerate(papers):
            # 1. 计算相关性分数（基于关键词匹配）
            title_abstract = f"{paper.title} {paper.abstract}".lower()
            
            # 计算关键词重叠度
            if self.use_keywords and query_keywords:
                paper_keywords = set(re.findall(r'\w+', title_abstract))
                overlap = len(query_keywords & paper_keywords) / max(len(query_keywords), 1)
                score = min(10.0, overlap * 15)  # 放大分数
                
                # 如果重叠度为 0，使用 BM25 分数作为后备
                if score == 0 and paper.score:
                    score = min(10.0, paper.score / 100.0)  # 简单归一化 BM25 分数
            else:
                # 使用已有的 ranker 分数（BM25 分数）
                # paper.score 是 BM25 分数，需要归一化到 0-10
                if paper.score:
                    # BM25 分数可能很大，需要归一化
                    score = min(10.0, paper.score / 100.0)  # 简单归一化
                else:
                    score = 5.0  # 默认分数
            
            # 2. 生成简短摘要（前 2 句）
            abstract = paper.abstract or ""
            sentences = re.split(r'[.!?]\s+', abstract)
            summary = ". ".join(sentences[:2]) + "." if len(sentences) >= 2 else abstract[:200]
            
            refined.append({
                "paper": paper,
                "score": round(score, 1),
                "summary": summary[:300],  # 限制长度
            })
        
        # 按分数排序
        refined.sort(key=lambda x: x["score"], reverse=True)
        
        return refined


class APILLMRefiner(LLMRefinerBase):
    """API 模式：调用外部 LLM API。"""
    
    def __init__(
        self,
        api_key: str = "",
        model: str = "deepseek-v4",
        base_url: str = "https://api.deepseek.com/v1/chat/completions",
    ) -> None:
        """
        初始化 API Refiner。
        
        Args:
            api_key: API 密钥（默认从环境变量 DEEPSEEK_API_KEY 读取）
            model: 模型名称（deepseek-v4 / gpt-4 / claude-3）
            base_url: API 地址
        """
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        self.model = model
        self.base_url = base_url
        
        if not self.api_key:
            raise ValueError(
                "APILLMRefiner: 需要 API Key。\n"
                "  1. 设置环境变量 DEEPSEEK_API_KEY\n"
                "  2. 或传入 api_key 参数"
            )
    
    def Refine(
        self,
        papers: List[Any],
        query: str,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """使用 API 对论文进行精细化筛选。"""
        import requests
        
        # 构建 prompt
        prompt = self._BuildPrompt(papers, query)
        
        # 调用 API
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        data = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 4096,
        }
        
        try:
            response = requests.post(
                self.base_url,
                headers=headers,
                json=data,
                timeout=120
            )
            response.raise_for_status()
            
            result = response.json()
            llm_output = result["choices"][0]["message"]["content"]
            
            # 解析结果
            return self._ParseResults(llm_output, papers)
            
        except Exception as e:
            note(f"[APILLMRefiner] API 调用失败: {e}")
            return []
    
    def _BuildPrompt(self, papers: List[Any], query: str) -> str:
        """构建 LLM Prompt。"""
        papers_text = ""
        for i, p in enumerate(papers, 1):
            papers_text += f"""
{i}. Title: {p.title}
   Abstract: {(p.abstract or "")[:500]}...
"""
        
        prompt = f"""You are a research paper reviewer.

Task: Filter and score the following papers based on their relevance to the query: "{query}"

For each paper, provide:
1. A relevance score (0-10, where 10 = highly relevant)
2. A brief 2-3 sentence summary

Output format (JSON only, no other text):
[
  {{"id": 1, "score": 8.5, "summary": "..."}},
  ...
]

Papers:
{papers_text}

Output only valid JSON, no other text.
"""
        return prompt
    
    def _ParseResults(self, llm_output: str, papers: List[Any]) -> List[Dict[str, Any]]:
        """解析 LLM 输出。"""
        # 提取 JSON（可能包含在 markdown 代码块中）
        json_match = re.search(r'```json\n(.*?)\n```', llm_output, re.DOTALL)
        if json_match:
            llm_output = json_match.group(1)
        
        try:
            results = json.loads(llm_output)
        except json.JSONDecodeError:
            note(f"[APILLMRefiner] 解析 LLM 输出失败: {llm_output[:200]}...")
            return []
        
        # 合并结果
        refined = []
        for r in results:
            idx = r.get("id", 0) - 1  # 1-based → 0-based
            if 0 <= idx < len(papers):
                refined.append({
                    "paper": papers[idx],
                    "score": r.get("score", 0.0),
                    "summary": r.get("summary", ""),
                })
        
        return refined


class AgentLLMRefiner(LLMRefinerBase):
    """Agent 模式：输出到 JSON，由本地 Agent 处理。
    
    工作流程：
    1. 将候选论文输出到 data/llm_refine_input.json
    2. 打印提示信息，要求 Agent 处理
    3. Agent 读取输入文件，评分并生成摘要
    4. Agent 将结果保存到 data/llm_refine_input_output.json
    5. 代码读取输出文件并返回结果
    """
    
    def __init__(
        self,
        input_file: str = "data/llm_refine_input.json",
        output_file: str = "data/llm_refine_input_output.json",
    ) -> None:
        """
        初始化 Agent Refiner。
        
        Args:
            input_file: 输入文件路径（候选论文）
            output_file: 输出文件路径（Agent 处理结果）
        """
        self.input_file = input_file
        self.output_file = output_file
    
    def Refine(
        self,
        papers: List[Any],
        query: str,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """输出到 JSON，等待 Agent 处理。"""
        # 1. 输出到 JSON
        self._OutputToJSON(papers, query)
        
        # 2. 打印提示信息
        self._PrintInstructions()
        
        # 3. 等待 Agent 处理（轮询输出文件）
        note(f"[AgentLLMRefiner] 等待 Agent 处理...")
        note(f"[AgentLLMRefiner] 请手动处理以下文件：")
        note(f"  输入: {self.input_file}")
        note(f"  输出: {self.output_file}")
        note(f"\n请使用本地 Agent 处理：")
        note(f"  1. 读取 {self.input_file}")
        note(f"  2. 对每篇论文评分 (0-10) 并生成摘要")
        note(f"  3. 保存到 {self.output_file}")
        note(f"\n处理完成后，再次运行命令并添加 --agent-read-result 参数。\n")
        
        # 返回空结果（需要再次运行命令读取结果）
        return []
    
    def _OutputToJSON(self, papers: List[Any], query: str) -> None:
        """输出候选论文到 JSON 文件。"""
        data = {
            "query": query,
            "papers": []
        }
        
        for i, p in enumerate(papers, 1):
            data["papers"].append({
                "id": i,
                "title": p.title,
                "authors": p.authors,
                "abstract": p.abstract,
                "arxiv_id": p.arxiv_id,
                "url": p.abs_url,
            })
        
        os.makedirs(os.path.dirname(self.input_file), exist_ok=True)
        with open(self.input_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        note(f"[AgentLLMRefiner] 已输出 {len(papers)} 篇论文到 {self.input_file}")
    
    def _PrintInstructions(self) -> None:
        """打印处理说明。"""
        print(f"\n{'='*80}")
        print(f"[AgentLLMRefiner] Agent 模式：需要手动处理")
        print(f"{'='*80}\n")
    
    def ReadResult(self) -> List[Dict[str, Any]]:
        """读取 Agent 处理结果。
        
        Returns:
            处理后的论文列表 [{"paper": Paper, "score": 8.5, "summary": "..."}, ...]
        """
        if not os.path.exists(self.output_file):
            note(f"[AgentLLMRefiner] 输出文件不存在: {self.output_file}")
            return []
        
        try:
            with open(self.output_file, "r", encoding="utf-8") as f:
                result_data = json.load(f)
            
            return self._ParseAgentResult(result_data)
        
        except Exception as e:
            note(f"[AgentLLMRefiner] 读取结果失败: {e}")
            return []
    
    def _ParseAgentResult(self, result_data: dict) -> List[Dict[str, Any]]:
        """解析 Agent 的输出结果。"""
        # 尝试相对导入，如果失败则使用绝对导入
        try:
            from ..protocol import Paper
        except (ImportError, ValueError):
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
            from scripts.lib.arxiv.protocol import Paper
        
        results = result_data.get("results", [])
        refined = []
        
        # 读取原始论文数据
        if not os.path.exists(self.input_file):
            note(f"[AgentLLMRefiner] 输入文件不存在: {self.input_file}")
            return []
        
        with open(self.input_file, "r", encoding="utf-8") as f:
            input_data = json.load(f)
        
        papers_dict = {p["id"]: p for p in input_data["papers"]}
        
        for r in results:
            idx = r.get("id", 0)
            if idx in papers_dict:
                p = papers_dict[idx]
                refined.append({
                    "paper": Paper(
                        arxiv_id=p["arxiv_id"],
                        title=p["title"],
                        authors=p["authors"],
                        abstract=p["abstract"],
                        categories=[],
                        primary_category="",
                        published="",
                        pdf_url="",
                        abs_url=p["url"],
                    ),
                    "score": r.get("score", 0.0),
                    "summary": r.get("summary", ""),
                })
        
        # 按分数排序
        refined.sort(key=lambda x: x["score"], reverse=True)
        
        return refined


def CreateLLMRefiner(
    mode: str = "heuristic",
    **kwargs
) -> LLMRefinerBase:
    """
    创建 LLM Refiner。
    
    Args:
        mode: 模式（"api" / "heuristic" / "agent"）
        **kwargs: 传递给具体实现类的参数
        
    Returns:
        LLMRefinerBase 实例
    """
    mode = mode.lower().strip()
    
    if mode == "api":
        return APILLMRefiner(
            api_key=kwargs.get("api_key", ""),
            model=kwargs.get("model", "deepseek-v4"),
            base_url=kwargs.get("base_url", ""),
        )
    elif mode == "heuristic":
        return HeuristicLLMRefiner(
            use_keywords=kwargs.get("use_keywords", True),
        )
    elif mode == "agent":
        return AgentLLMRefiner(
            input_file=kwargs.get("input_file", "data/llm_refine_input.json"),
            output_file=kwargs.get("output_file", "data/llm_refine_input_output.json"),
        )
    else:
        raise ValueError(f"不支持的 LLM 模式: {mode}（支持: api, heuristic, agent）")


if __name__ == "__main__":
    # 测试
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    
    # 创建测试论文
    from protocol import Paper
    
    test_papers = [
        Paper(
            arxiv_id="2506.12345",
            title="Dynamic Dexterous Grasping with Diffusion Policy",
            authors=["Author A", "Author B"],
            abstract="This paper proposes a novel diffusion-based approach for dexterous grasping...",
            categories=["cs.RO"],
            primary_category="cs.RO",
            published="2026-06-10",
            pdf_url="https://arxiv.org/pdf/2506.12345",
            abs_url="https://arxiv.org/abs/2506.12345",
        ),
        Paper(
            arxiv_id="2506.12346",
            title="Event-based SLAM for Sparse Visual Odometry",
            authors=["Author C"],
            abstract="We present an event camera-based SLAM system...",
            categories=["cs.CV"],
            primary_category="cs.CV",
            published="2026-06-10",
            pdf_url="https://arxiv.org/pdf/2506.12346",
            abs_url="https://arxiv.org/abs/2506.12346",
        ),
    ]
    
    # 测试 Heuristic 模式
    print("测试 Heuristic 模式...")
    refiner = CreateLLMRefiner(mode="heuristic")
    results = refiner.Refine(test_papers, query="dexterous manipulation")
    
    print(f"✅ Heuristic 模式: {len(results)} 篇论文")
    for r in results[:2]:
        print(f"  - Score: {r['score']:.1f} | {r['paper'].title[:50]}...")
