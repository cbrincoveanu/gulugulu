# Gulugulu

Gulugulu is a minimalist, anti-SEO search engine designed to surface the "Old Web." It mimics the "Google from 10 years ago" aesthetic: signal-rich, unoptimized, and brutally simple.

The goal is to provide a search experience that focuses on niche, personal, and high-signal websites, avoiding the AI-generated SEO spam that dominates modern search results.

## Live Demo

Check it out here: [https://cbrincoveanu.github.io/gulugulu/](https://cbrincoveanu.github.io/gulugulu/)

## Project Structure

- `frontend/`: The search interface and the client-side data index.
- `crawler/`: Python backend for discovering, filtering, and indexing sites.

## Frontend

The frontend is a purely static site that uses `Fuse.js` for client-side searching. It fetches a pre-generated `index.json` and filters it in real-time.

### Running Locally

To run the frontend, navigate to the `frontend` directory and start a web server:

```bash
cd frontend
python3 -m http.server 8000
```

Then visit `http://localhost:8000`.

## Crawler

The crawler fetches data from RSS feeds and specific websites to populate `frontend/index.json`. It features an optional LLM-based filtering and enrichment layer to ensure high-quality results.

### Setup

Install dependencies:

```bash
pip install -r crawler/requirements.txt
```

### Running the Crawler

1. Create a `.env` file in the `crawler/` directory (see `.env.example`).
2. Run the script directly:

```bash
python3 crawler/crawler.py
```

### LLM Enrichment

The crawler can use a local LLM (via OpenAI-compatible APIs like Ollama or LM Studio) to filter sites and generate optimized descriptions. Configure `OPENAI_API_BASE` in `crawler/.env` to enable this feature.

The crawler assigns a **Quality Score** (1-100) to each site. The final `index.json` is automatically limited to the top 10,000 items to ensure optimal frontend performance.

### Running with Docker

You can run the crawler in a container. To ensure the generated `index.json` is saved to your host machine, mount the `frontend` directory and pass the environment variables:

```bash
docker build -t gulugulu-crawler crawler/
docker run --env-file crawler/.env -v $(pwd)/frontend:/frontend gulugulu-crawler
```

## Adding Sources

Edit `crawler/sources.json` to add new RSS feeds or websites to be indexed.

```json
{
  "feeds": ["https://example.com/rss"],
  "sites": [
    {
      "url": "https://example.com",
      "crawl_depth": [1],
      "follow_internal": true,
      "follow_external": false
    }
  ]
}
```

- `crawl_depth`: An integer (0-N) or a list of integers (e.g., `[1]`) specifying which depths to index.
- `follow_internal`: Whether to follow links within the same domain on the seed page.
- `follow_external`: Whether to follow external links on the seed page (useful for directories).

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
