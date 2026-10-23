#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenReview Client — Fetch papers from OpenReview (conference submissions).
"""

from __future__ import annotations

import json
import time
from typing import List, Dict, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from ..protocol import Paper


class OpenReviewClient:
    """Client for OpenReview API.
    
    API Documentation:
    - Endpoint: https://api2.openreview.net/notes
    - Parameters: invitation, limit, offset, details
    """
    
    def __init__(self, base_url: str = "https://api2.openreview.net", timeout: int = 30) -> None:
        """
        Initialize OpenReview client.
        
        Args:
            base_url: OpenReview API base URL
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
    
    def FetchByConference(self, conference: str, year: int = 2025, max_results: int = 100) -> List[Paper]:
        """
        Fetch papers by conference and year.
        
        Args:
            conference: Conference short name (e.g., "NeurIPS", "ICML", "ICLR")
            year: Conference year
            max_results: Maximum number of papers to return
            
        Returns:
            List of Paper objects
        """
        # Build invitation pattern
        invitation = f"{conference}.cc/{year}/Conference/-/Submission"
        
        url = f"{self.base_url}/notes?invitation={invitation}&limit={min(max_results, 1000)}&details=replyCount,invitation"
        
        try:
            papers_data = self._FetchWithRetry(url)
            if not papers_data or "notes" not in papers_data:
                return []
            
            papers = []
            for note in papers_data["notes"][:max_results]:
                paper = self._ParsePaper(note, conference, year)
                if paper:
                    papers.append(paper)
                    
            return papers
            
        except Exception as e:
            import sys
            print(f"[OpenReview] Error fetching papers: {e}", file=sys.stderr)
            return []
    
    def FetchRecent(self, days: int = 30, max_results: int = 100) -> List[Paper]:
        """
        Fetch recent submissions (across all conferences).
        
        Args:
            days: Not directly supported by OpenReview API, use FetchByConference instead
            max_results: Maximum number of papers to return
            
        Returns:
            List of Paper objects
        """
        # OpenReview doesn't have a "recent" endpoint
        # This is a convenience method that tries common conferences
        conferences = ["NeurIPS", "ICML", "ICLR", "CVPR", "ICCV"]
        papers = []
        
        for conf in conferences:
            if len(papers) >= max_results:
                break
                
            conf_papers = self.FetchByConference(conf, 2025, max_results - len(papers))
            papers.extend(conf_papers)
            
        return papers[:max_results]
    
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
                    return data
                    
            except (HTTPError, URLError) as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
                    continue
                else:
                    raise
        
        return None
    
    def _ParsePaper(self, note: Dict, conference: str, year: int) -> Optional[Paper]:
        """
        Parse a single paper note from OpenReview API response.
        
        Args:
            note: Paper data from API
            conference: Conference name
            year: Conference year
            
        Returns:
            Paper object or None
        """
        try:
            content = note.get("content", {})
            
            # OpenReview API returns nested structure: {"value": "..."}
            title = content.get("title", {}).get("value", "").strip()
            abstract = content.get("abstract", {}).get("value", "").strip()
            
            # Authors - nested structure
            authors_raw = content.get("authors", {}).get("value", [])
            if isinstance(authors_raw, list):
                authors = [a.strip() for a in authors_raw if isinstance(a, str)]
            else:
                authors = []
            
            # Build URLs
            note_id = note.get("id", "")
            url = f"https://openreview.net/forum?id={note_id}"
            
            # Categories
            categories = [f"{conference}.{year}"]
            
            return Paper(
                arxiv_id=note_id,
                title=title,
                authors=authors,
                abstract=abstract,
                categories=categories,
                primary_category=categories[0],
                published=str(note.get("cdate", "")),
                pdf_url=f"https://openreview.net/pdf?id={note_id}",
                abs_url=url,
                doi=f"openreview:{note_id}",
                score=0.0,
                is_new=True,
                code_url="",
                comment=f"OpenReview {conference} {year}",
            )
            
        except Exception as e:
            import sys
            print(f"[OpenReview] Error parsing paper: {e}", file=sys.stderr)
            return None


def CreateOpenReviewClient(base_url: str = "https://api2.openreview.net") -> OpenReviewClient:
    """
    Factory function to create an OpenReviewClient instance.
    
    Args:
        base_url: OpenReview API base URL
        
    Returns:
        OpenReviewClient instance
    """
    return OpenReviewClient(base_url=base_url)
