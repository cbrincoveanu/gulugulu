# Gulugulu Agent Guide

## Overview
Gulugulu is a minimalist, anti-SEO search engine designed to surface the "Old Web." It mimics the "Google from 10 years ago" aesthetic: signal-rich, unoptimized, and brutally simple.

## Architecture
- **Frontend** (`/frontend`): Static HTML/JS. No heavy frameworks. Uses `Fuse.js` for client-side search.
- **Crawler** (`/crawler`): Python backend that generates `frontend/index.json` from RSS feeds and site links. Supports LLM-based filtering and enrichment via OpenAI-compatible APIs.

## Key Files
- `frontend/index.html`: The main search interface.
- `frontend/index.json`: The data store for search results.
- `crawler/crawler.py`: Logic for fetching and indexing new sites.
- `crawler/sources.json`: Configuration for search sources.
- `crawler/.env`: Configuration for LLM (OpenAI-compatible API).

## Development Commands
- **Frontend**: `python3 -m http.server` inside `frontend/`.
- **Crawler**: `python3 crawler/crawler.py` (ensure `.env` is set up).
- **Docker**: `docker run --env-file crawler/.env -v $(pwd)/frontend:/frontend $(docker build -q crawler/)`.

## Constraints & Mistakes to Avoid
- **LLM Fallback**: The crawler should fall back to metadata if the LLM is unavailable.
- **LLM Override**: For `crawl_depth` 0 (the seed URL), the LLM's `keep` decision is overridden (always kept), but it still optimizes the metadata.
- **Crawl Depth**: `crawl_depth` in `sources.json` can be an integer (0-N) or a list (e.g. `[1]`) to target specific depths.
- **Safe Directory Crawl**: At depth 0 (seed URL), the crawler follows *all* links (internal and external) to discover new sites by default. This is configurable via `follow_internal` and `follow_external` in `sources.json`. At depth > 0, it stays within the current domain to avoid "leaking" to other sites.
- **Real-time Logs**: `ENV PYTHONUNBUFFERED=1` in the Dockerfile ensures real-time log output.
- **Maintain Minimalism**: Keep the HTML semantic and the CSS sparse. No tracking or ads.
- **No IDs**: `index.json` entries do not need an `id` field. The frontend uses `url` as the unique identifier.

- **Relative Paths**: The crawler script must correctly target `../frontend/index.json`.
- **Deduplication**: The crawler must prevent duplicate URLs in `index.json`.
- **Global BFS**: The crawler uses a global Breadth-First Search queue to discover sites broadly before going deep.
- **Decoupled Discovery with Backpressure**: Separate thread pools for network fetching (`MAX_CONCURRENT_FETCH`) and LLM processing (`MAX_CONCURRENT_LLM`). Fetchers are throttled if the LLM queue is too long to prevent excessive memory usage.
- **Persistence & Resumption**: Results are periodically saved to `progress.jsonl`. On startup, the crawler loads this file to resume progress and skip already processed URLs.
- **Quality Scaling**: The crawler uses an LLM to assign a `quality_score` (1-100). The final `index.json` is limited to the top 10,000 items (configurable via `MAX_INDEX_SIZE`) sorted by this score.
- **Enhanced Keywords**: The LLM is forced to output 5-7 specific conceptual tags to improve Fuse.js search matching.
