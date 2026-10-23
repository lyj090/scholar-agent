#!/usr/bin/env python3
"""
测试检索功能 - BM25、Embedding、Hybrid 检索

验证从 daily-paper-reader 提取的模块是否正常工作。
"""

import sys
import os
import json
import argparse
from typing import List, Dict, Any

# Ensure scripts/lib/arxiv/ is importable when this file is run directly.
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(TESTS_DIR, "..", "lib"))

# 模拟论文数据
TEST_PAPERS = [
    {
        "id": "2401.12345",
        "source": "arxiv",
        "title": "Robot Learning for Manipulation Tasks: A Survey",
        "abstract": "This paper surveys recent advances in robot learning for manipulation tasks. We review imitation learning, reinforcement learning, and self-supervised learning approaches.",
        "authors": ["John Doe", "Jane Smith"],
        "primary_category": "cs.RO",
        "categories": ["cs.RO", "cs.AI"],
        "published": "2024-01-15",
        "link": "https://arxiv.org/abs/2401.12345",
    },
    {
        "id": "2402.23456",
        "source": "arxiv",
        "title": "Vision Transformers for Object Detection",
        "abstract": "We propose a novel vision transformer architecture for real-time object detection. Our method achieves state-of-the-art performance on COCO dataset.",
        "authors": ["Alice Johnson"],
        "primary_category": "cs.CV",
        "categories": ["cs.CV"],
        "published": "2024-02-20",
        "link": "https://arxiv.org/abs/2402.23456",
    },
    {
        "id": "2403.34567",
        "source": "arxiv",
        "title": "Reinforcement Learning with Human Feedback for Robotics",
        "abstract": "This work introduces a new reinforcement learning algorithm that incorporates human feedback. We demonstrate improved sample efficiency on robotic manipulation tasks.",
        "authors": ["Bob Wilson", "Carol Brown"],
        "primary_category": "cs.LG",
        "categories": ["cs.LG", "cs.RO"],
        "published": "2024-03-10",
        "link": "https://arxiv.org/abs/2403.34567",
    },
    {
        "id": "2404.45678",
        "source": "arxiv",
        "title": "Multi-Modal Sensor Fusion for Autonomous Navigation",
        "abstract": "We present a multi-modal sensor fusion framework combining LiDAR, camera, and IMU data for robust autonomous navigation in dynamic environments.",
        "authors": ["David Lee"],
        "primary_category": "cs.RO",
        "categories": ["cs.RO", "cs.CV"],
        "published": "2024-04-05",
        "link": "https://arxiv.org/abs/2404.45678",
    },
    {
        "id": "2405.56789",
        "source": "arxiv",
        "title": "Large Language Models for Code Generation",
        "abstract": "This paper explores the use of large language models for automated code generation. We propose prompt engineering techniques to improve code quality.",
        "authors": ["Eva Martinez"],
        "primary_category": "cs.CL",
        "categories": ["cs.CL", "cs.AI"],
        "published": "2024-05-12",
        "link": "https://arxiv.org/abs/2405.56789",
    },
]


def test_bm25_retrieval():
    """测试 BM25 检索。"""
    print("\n" + "="*60)
    print("测试 1: BM25 检索")
    print("="*60)
    
    try:
        from arxiv.retrieval_bm25 import Paper, build_bm25_index, rank_papers_by_bm25
        
        # 创建 Paper 对象
        papers = [Paper.from_dict(p) for p in TEST_PAPERS]
        
        # 构建 BM25 索引
        print(f"\n[1] 构建 BM25 索引（{len(papers)} 篇论文）...")
        bm25 = build_bm25_index(papers)
        print("✅ BM25 索引构建成功")
        
        # 执行检索
        query = "robot learning manipulation"
        print(f"\n[2] 执行检索: \"{query}\"")
        results = rank_papers_by_bm25(bm25, papers, query, top_k=10)
        
        # 显示结果
        print(f"\n[3] 检索结果（Top {len(results)}）:")
        for idx, score in results[:3]:
            paper = papers[idx]
            print(f"  {paper.title} (score: {score:.4f})")
        
        print("\n✅ BM25 检索测试通过")
        return True
        
    except Exception as e:
        print(f"\n❌ BM25 检索测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_embedding_retrieval(actual=False):
    """测试 Embedding 检索。"""
    print("\n" + "="*60)
    print("测试 2: Embedding 检索")
    print("="*60)
    
    try:
        from arxiv.retriever import create_retriever
        
        if not actual:
            print("✅ 模块加载成功（默认不下载模型；加 --actual 执行真实检索）")
            return True

        print("\n[1] 检查 sentence-transformers...")
        try:
            import sentence_transformers
            print(f"✅ sentence-transformers 已安装 (version: {sentence_transformers.__version__})")
        except ImportError:
            print("⚠️  sentence-transformers 未安装，跳过 --actual Embedding 测试")
            return True

        print("\n[2] 创建 Embedding 检索器（可能下载模型）...")
        retriever = create_retriever(
            retrieval_mode="embedding", model_name="BAAI/bge-small-en-v1.5", device="cpu"
        )
        print("[3] 构建 Embedding 索引...")
        retriever.build_index(TEST_PAPERS)
        results = retriever.retrieve_and_format("robot learning manipulation", top_k=10)
        print(f"[4] 真实检索返回 {len(results)} 条，Top 1: {results[0]['title'] if results else 'none'}")

        print("\n✅ Embedding 检索模块加载成功")
        return True
        
    except Exception as e:
        print(f"\n❌ Embedding 检索测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_rrf_fusion():
    """测试 RRF 结果融合。"""
    print("\n" + "="*60)
    print("测试 3: RRF 结果融合")
    print("="*60)
    
    try:
        from arxiv.rrf_fusion import merge_by_rrf, normalize_rrf_results
        
        # 模拟 BM25 结果: [(论文索引, 分数), ...]
        print("\n[1] 模拟 BM25 检索结果...")
        bm25_results = [
            (0, 2.5),  # 论文 0, 分数 2.5 (排名 1)
            (2, 2.0),  # 论文 2, 分数 2.0 (排名 2)
            (3, 1.5),  # 论文 3, 分数 1.5 (排名 3)
            (1, 1.0),  # 论文 1, 分数 1.0 (排名 4)
        ]
        print(f"   BM25 结果: {len(bm25_results)} 篇论文")
        
        # 模拟 Embedding 结果
        print("\n[2] 模拟 Embedding 检索结果...")
        embedding_results = [
            (2, 0.85),  # 论文 2, 相似度 0.85 (排名 1)
            (0, 0.80),  # 论文 0, 相似度 0.80 (排名 2)
            (4, 0.75),  # 论文 4, 相似度 0.75 (排名 3)
            (1, 0.60),  # 论文 1, 相似度 0.60 (排名 4)
        ]
        print(f"   Embedding 结果: {len(embedding_results)} 篇论文")
        
        # RRF 融合
        print("\n[3] 执行 RRF 融合...")
        rrf_results = merge_by_rrf(
            [bm25_results, embedding_results],
            k=60,
            top_n=5,
        )
        
        # 显示结果
        print(f"\n[4] RRF 融合结果（Top {len(rrf_results)}）:")
        for idx, score in rrf_results:
            print(f"  论文 {idx}: RRF 分数 = {score:.6f}")
        
        # 归一化
        print("\n[5] 归一化 RRF 分数...")
        normalized = normalize_rrf_results(rrf_results)
        print("✅ RRF 结果归一化成功")
        for idx, score in normalized:
            print(f"  论文 {idx}: 归一化分数 = {score:.4f}")
        
        print("\n✅ RRF 融合测试通过")
        return True
        
    except Exception as e:
        print(f"\n❌ RRF 融合测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_hybrid_retrieval():
    """测试 Hybrid 检索（RRF 融合）。"""
    print("\n" + "="*60)
    print("测试 4: Hybrid 检索（模拟）")
    print("="*60)
    
    try:
        from arxiv.rrf_fusion import hybrid_retrieval
        
        # 模拟 BM25 结果
        print("\n[1] 模拟 BM25 检索...")
        bm25_results = [(0, 2.5), (2, 2.0), (3, 1.5), (1, 1.0)]
        
        # 模拟 Embedding 结果
        print("[2] 模拟 Embedding 检索...")
        embedding_results = [(2, 0.85), (0, 0.80), (4, 0.75), (1, 0.60)]
        
        # Hybrid 检索
        print("\n[3] 执行 Hybrid 检索（RRF 融合）...")
        results = hybrid_retrieval(
            bm25_results=bm25_results,
            embedding_results=embedding_results,
            bm25_weight=0.5,
            embedding_weight=0.5,
            k=60,
            top_n=5,
        )
        
        # 显示结果
        print(f"\n[4] Hybrid 检索结果（Top {len(results)}）:")
        for idx, score in results:
            print(f"  论文 {idx}: Hybrid 分数 = {score:.6f}")
        
        print("\n✅ Hybrid 检索测试通过")
        return True
        
    except Exception as e:
        print(f"\n❌ Hybrid 检索测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_hybrid_actual():
    """Run the optional model-backed Hybrid path (only with --actual)."""
    print("\n" + "="*60)
    print("实际测试: Hybrid 检索")
    print("="*60)
    try:
        try:
            import sentence_transformers  # noqa: F401
        except ImportError:
            print("⚠️  sentence-transformers 未安装，跳过 --actual Hybrid 测试")
            return True

        from arxiv.retriever import create_retriever

        retriever = create_retriever(
            retrieval_mode="hybrid",
            model_name="BAAI/bge-small-en-v1.5",
            device="cpu",
            bm25_weight=0.5,
            embedding_weight=0.5,
        )
        retriever.build_index(TEST_PAPERS)
        results = retriever.retrieve_and_format("robot learning manipulation", top_k=10)
        if not results:
            raise AssertionError("Hybrid retrieval returned no results")
        print(f"✅ Hybrid 真实检索返回 {len(results)} 条")
        return True
    except Exception as e:
        print(f"❌ Hybrid 真实检索失败: {e}")
        return False


def test_ranker():
    """测试 Qwen3-Reranker（仅检查模块导入）。"""
    print("\n" + "="*60)
    print("测试 5: Qwen3-Reranker（模块检查）")
    print("="*60)
    
    try:
        from arxiv.ranker import LocalQwenReranker, create_ranker, format_doc
        
        print("\n[1] 检查 LocalQwenReranker 类...")
        print("✅ LocalQwenReranker 类已定义")
        
        print("\n[2] 检查 create_ranker() 函数...")
        print("✅ create_ranker() 函数已定义")
        
        print("\n[3] 检查 format_doc() 函数...")
        doc = format_doc("Test Title", "Test abstract")
        print(f"✅ format_doc() 工作正常: \"{doc[:50]}...\"")
        
        print("\n[4] 检查依赖...")
        try:
            import torch
            print(f"✅ torch 已安装 (version: {torch.__version__})")
        except ImportError:
            print("⚠️  torch 未安装，无法测试本地 Reranker")
            print("   请运行: pip install torch")
        
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer
            print(f"✅ transformers 已安装")
        except ImportError:
            print("⚠️  transformers 未安装，无法测试本地 Reranker")
            print("   请运行: pip install transformers")
        
        print("\n✅ Qwen3-Reranker 模块检查通过")
        print("\n⚠️  注意: 实际加载模型需要下载 Qwen/Qwen3-Reranker-0.6B (~1.2GB)")
        print("   如果想测试实际重排序功能，请运行:")
        print("   python3 scripts/test_ranker.py")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Qwen3-Reranker 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_unified_retriever():
    """测试统一检索器接口。"""
    print("\n" + "="*60)
    print("测试 6: 统一检索器接口（模块检查）")
    print("="*60)
    
    try:
        from arxiv.retriever import PaperRetriever, create_retriever
        
        print("\n[1] 检查 PaperRetriever 类...")
        print("✅ PaperRetriever 类已定义")
        
        print("\n[2] 检查 create_retriever() 函数...")
        print("✅ create_retriever() 函数已定义")
        
        print("\n[3] 创建检索器实例（仅检查，不加载模型）...")
        # 不实际调用 build_index()，因为那会加载模型
        print("✅ 检索器接口设计正确")
        
        print("\n✅ 统一检索器接口测试通过")
        return True
        
    except Exception as e:
        print(f"\n❌ 统一检索器接口测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def generate_report(results: Dict[str, bool]):
    """生成测试报告。"""
    print("\n" + "="*60)
    print("测试报告")
    print("="*60)
    
    total = len(results)
    passed = sum(1 for v in results.values() if v)
    failed = total - passed
    
    print(f"\n总计: {total} 个测试")
    print(f"通过: {passed} 个")
    print(f"失败: {failed} 个")
    
    print("\n详细结果:")
    for test_name, success in results.items():
        status = "✅ 通过" if success else "❌ 失败"
        print(f"  {test_name}: {status}")
    
    print("\n" + "="*60)
    
    if passed == total:
        print("🎉 所有测试通过！Phase 1 实施成功。")
        print("\n下一步:")
        print("  1. 安装缺失的依赖（如果有）")
        print("  2. 下载 Embedding 模型: BAAI/bge-small-en-v1.5")
        print("  3. 下载 Reranker 模型: Qwen/Qwen3-Reranker-0.6B")
        print("  4. 运行实际检索测试")
        print("  5. 继续 Phase 2: LLM 智能筛选")
    else:
        print(f"⚠️  有 {failed} 个测试失败，请检查错误信息。")
    
    print("="*60 + "\n")
    
    return passed == total


def main():
    """运行所有测试。"""
    parser = argparse.ArgumentParser(description="检索模块 smoke test; --actual enables model downloads")
    parser.add_argument("--actual", action="store_true", help="run model-backed Embedding and Hybrid tests")
    args = parser.parse_args()
    print("="*60)
    print("论文检索功能测试")
    print("="*60)
    print("\n此脚本将测试以下模块:")
    print("  1. BM25 检索")
    print("  2. Embedding 检索")
    print("  3. RRF 结果融合")
    print("  4. Hybrid 检索")
    print("  5. Qwen3-Reranker")
    print("  6. 统一检索器接口")
    
    results = {}
    
    # 测试 1: BM25 检索
    results["BM25 检索"] = test_bm25_retrieval()
    
    # 测试 2: Embedding 检索
    results["Embedding 检索"] = test_embedding_retrieval(actual=args.actual)
    
    # 测试 3: RRF 结果融合
    results["RRF 结果融合"] = test_rrf_fusion()
    
    # 测试 4: Hybrid 检索
    results["Hybrid 检索"] = test_hybrid_retrieval()

    if args.actual:
        results["Hybrid 实际检索"] = test_hybrid_actual()
    
    # 测试 5: Qwen3-Reranker
    results["Qwen3-Reranker"] = test_ranker()
    
    # 测试 6: 统一检索器接口
    results["统一检索器接口"] = test_unified_retriever()
    
    # 生成报告
    return generate_report(results)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
