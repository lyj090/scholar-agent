#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BioRxiv Client — Fetch papers from bioRxiv/medRxiv.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from ..protocol import Paper


class BioRxivClient:
    """Client for bioRxiv/medRxiv API.
    
    API Documentation:
    - Endpoint: https://api.biorxiv.org/details/{server}/{interval}/{cursor}
    - Server: "biorxiv" or "medrxiv"
    - Interval: YYYY-MM-DD/YYYY-MM-DD (date range)
    - Cursor: 0, 100, 200, ... (pagination)
    """
    
    def __init__(self, server: str = "biorxiv", timeout: int = 30) -> None:
        """
        Initialize BioRxiv client.
        
        Args:
            server: "biorxiv" or "medrxiv"
            timeout: Request timeout in seconds
        """
        self.server = server.lower()
        self.timeout = timeout
        self.base_url = f"https://api.biorxiv.org/details/{self.server}"
    
    def FetchRecent(self, days: int = 1, max_results: int = 100) -> List[Paper]:
        """
        Fetch recent papers from bioRxiv.
        
        Args:
            days: Number of days to look back
            max_results: Maximum number of papers to return
            
        Returns:
            List of Paper objects
        """
        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        date_range = f"{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
        url = f"{self.base_url}/{date_range}/0"
        
        try:
            papers_data = self._FetchWithRetry(url)
            if not papers_data or "collection" not in papers_data:
                return []
            
            papers = []
            collection = papers_data.get("collection", [])
            
            for item in collection[:max_results]:
                paper = self._ParsePaper(item)
                if paper:
                    papers.append(paper)
                
                if len(papers) >= max_results:
                    break
                    
            return papers
            
        except Exception as e:
            import sys
            print(f"[BioRxiv] Error fetching papers: {e}", file=sys.stderr)
            return []
    
    def FetchByCategory(self, category: str, days: int = 7, max_results: int = 100) -> List[Paper]:
        """
        Fetch papers by category (subject area).
        
        Args:
            category: Subject area (e.g., "robotics", "bioinformatics")
            days: Number of days to look back
            max_results: Maximum number of papers to return
            
        Returns:
            List of Paper objects
        """
        # BioRxiv doesn't have direct category filtering via API
        # Fetch all recent and filter by title/abstract
        papers = self.FetchRecent(days, max_results * 2)
        
        if not category:
            return papers[:max_results]
            
        category_lower = category.lower()
        filtered = []
        
        for paper in papers:
            title_abstract = f"{paper.title} {paper.abstract}".lower()
            if category_lower in title_abstract:
                filtered.append(paper)
                if len(filtered) >= max_results:
                    break
                    
        return filtered
    
    def _FetchWithRetry(self, url: str, max_retries: int = 3) -> Optional[Dict]:
        """
        Fetch JSON data with retry logic.
        
        Args:
            url: URL to fetch
            max_retries: Maximum number of retries
            
        Returns:
            Parsed JSON data or None
        """
        for attempt in range(max_retries):
            try:
                req = Request(url)
                req.add_header("User-Agent", "scholar-agent/1.0")
                
                with urlopen(req, timeout=self.timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
                    
                    # Check API response status
                    if data.get("messages") and data["messages"][0].get("status") == "ok":
                        return data
                    else:
                        return None
                        
            except (HTTPError, URLError) as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
                    continue
                else:
                    raise
        
        return None
    
    def _ParsePaper(self, item: Dict) -> Optional[Paper]:
        """
        Parse a single paper item from bioRxiv API response.
        
        Args:
            item: Paper data from API
            
        Returns:
            Paper object or None
        """
        try:
            arxiv_id = item.get("doi", "")
            title = item.get("title", "").strip()
            authors_str = item.get("authors", "")
            abstract = item.get("abstract", "").strip()
            published = item.get("date", "")
            url = item.get("link_page", f"https://www.biorxiv.org/content/{arxiv_id}v1")
            
            # Parse authors
            authors = [a.strip() for a in authors_str.split(";") if a.strip()]
            
            # Build categories
            categories = []
            if item.get("category"):
                categories.append(item["category"])
                
            return Paper(
                arxiv_id=arxiv_id,
                title=title,
                authors=authors,
                abstract=abstract,
                categories=categories,
                primary_category=categories[0] if categories else "",
                published=published,
                pdf_url=f"https://www.biorxiv.org/content/{arxiv_id}v1.full.pdf",
                abs_url=url,
                doi=arxiv_id,
                score=0.0,
                is_new=True,
                code_url="",
                comment="",
            )
            
        except Exception as e:
            import sys
            print(f"[BioRxiv] Error parsing paper: {e}", file=sys.stderr)
            return None


def CreateBioRxivClient(server: str = "biorxiv") -> BioRxivClient:
    """
    Factory function to create a BioRxivClient instance.
    
    Args:
        server: "biorxiv" or "medrxiv"
        
    Returns:
        BioRxivClient instance
    """
    return BioRxivClient(server=server)
