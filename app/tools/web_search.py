"""The Research Agent's web search tool, moved from
stage25_react_ui/backend/main.py (lines 125-134).

Phase 2 (async conversion): DuckDuckGoSearchRun (langchain_community) wraps
`ddgs`, a synchronous library that makes a blocking HTTP call - it has no
native async support. `AsyncDuckDuckGoSearchRun` overrides `_arun` to
explicitly run the blocking `_run` call via `asyncio.to_thread(...)`, so a
concurrent request never has the event loop blocked waiting on this search.
(BaseTool's own default `_arun` already runs `_run` in an executor, which
has the same practical effect - this override exists to make that off-loop
behavior explicit in this file rather than leaving it implicit in a
LangChain base class, matching this project's readable-over-clever
convention.)
"""

import asyncio

from langchain_community.tools import DuckDuckGoSearchRun


class AsyncDuckDuckGoSearchRun(DuckDuckGoSearchRun):
    async def _arun(self, query: str, run_manager=None) -> str:
        return await asyncio.to_thread(self._run, query)


search_web = AsyncDuckDuckGoSearchRun()
