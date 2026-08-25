"""The Research Agent's web search tool, moved from
stage25_react_ui/backend/main.py (lines 125-134)."""

from langchain_community.tools import DuckDuckGoSearchRun

search_web = DuckDuckGoSearchRun()
