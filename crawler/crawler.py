import requests
from bs4 import BeautifulSoup
import feedparser
import json
import os
import re
import threading
import time
import gc
from collections import deque
from urllib.parse import urljoin, urlparse
from dotenv import load_dotenv
from openai import OpenAI
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm

load_dotenv()

# Setup Config
llm_enabled = os.getenv("OPENAI_API_BASE") is not None
max_llm_workers = int(os.getenv("MAX_CONCURRENT_LLM", 5))
max_fetch_workers = int(os.getenv("MAX_CONCURRENT_FETCH", 20))
max_total_visits = int(os.getenv("MAX_TOTAL_VISITS", 50000))
max_index_size = int(os.getenv("MAX_INDEX_SIZE", 10000))
write_batch_size = int(os.getenv("WRITE_BATCH_SIZE", 100))
progress_file = os.getenv("PROGRESS_FILE", "progress.jsonl")

if llm_enabled:
    print(f"LLM enabled: {os.getenv('OPENAI_API_BASE')}")
    client = OpenAI(
        base_url=os.getenv("OPENAI_API_BASE"),
        api_key=os.getenv("OPENAI_API_KEY", "no-key")
    )
    llm_model = os.getenv("LLM_MODEL", "llama3")
else:
    print("LLM disabled")

DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# Results storage
all_results = {} # url -> item
results_lock = threading.Lock()
pending_save_count = 0

def normalize_domain(domain):
    if domain.startswith('www.'):
        return domain[4:]
    return domain

def clean_url(url):
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            return None
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if parsed.query:
            clean += f"?{parsed.query}"
        return clean
    except Exception:
        return None

def save_progress():
    global pending_save_count
    with results_lock:
        with open(progress_file, 'w') as f:
            for item in all_results.values():
                f.write(json.dumps(item) + '\n')
        pending_save_count = 0
    # print(f"\nProgress saved to {progress_file}")

def load_progress():
    global all_results
    if os.path.exists(progress_file):
        print(f"Loading existing progress from {progress_file}...")
        count = 0
        with open(progress_file, 'r') as f:
            for line in f:
                item = json.loads(line)
                all_results[item['url']] = item
                count += 1
        print(f"Loaded {count} processed items.")
        return set(all_results.keys())
    return set()

def process_with_llm(data, force_keep=False):
    if not llm_enabled:
        return {**data, "quality_score": 50}, True

    prompt = f"""Analyze the following website data for a curated search engine called 'Gulugulu' which surfaces the 'Old Web'.
Target Aesthetic: niche, personal, signal-rich, unoptimized, interesting, weird, non-commercial, and absolutely NO SEO spam or AI-generated filler.

Data:
URL: {data['url']}
Title: {data['title']}
Meta Description: {data['description']}
Text Content (Snippet): {data['raw_text']}

Task:
1. Decide if this site belongs in the index.
2. Synthesize a highly descriptive, search-relevant summary (1-2 sentences).
3. Generate 5-7 specific, conceptual keywords/tags that would help a user find this site (e.g., 'indieweb', 'retro-computing', 'personal-digital-garden').
4. Assign a 'Web Quality Score' (1-100) based on how well it fits the 'Old Web' criteria.

Respond ONLY with a JSON object:
{{
  "keep": true,
  "title": "Cleaned Title",
  "description": "Synthesized descriptive summary",
  "keywords": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "quality_score": 85
}}"""

    try:
        response = client.chat.completions.create(
            model=llm_model,
            messages=[
                {"role": "system", "content": "You are a curated web directory editor for the 'Old Web'. You output strictly valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0,
            response_format={ "type": "json_object" } if "gpt-4" in llm_model or "gpt-3.5" in llm_model else None
        )
        
        content = response.choices[0].message.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
            
        parsed = json.loads(content.strip())
        
        keep = parsed.get("keep")
        if isinstance(keep, str):
            keep = str(keep).lower() == 'true'
            
        if force_keep:
            keep = True

        if keep is False:
            return None, False
        
        return {
            "title": parsed.get("title", data["title"]),
            "url": data["url"],
            "description": parsed.get("description", data["description"]),
            "keywords": parsed.get("keywords", data["keywords"]),
            "quality_score": int(parsed.get("quality_score", 0))
        }, True
    except Exception:
        return {**data, "quality_score": 0}, True

def fetch_and_discover(url, depth, breadcrumbs, config):
    try:
        response = requests.get(url, headers=DEFAULT_HEADERS, timeout=10, stream=True)
        response.raise_for_status()
        
        content_type = response.headers.get('Content-Type', '').lower()
        if 'text/html' not in content_type:
            return None, []

        content = response.content
        soup = BeautifulSoup(content, 'html.parser')
        
        title = soup.title.string if soup.title else url
        description = ""
        desc_tag = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
        if desc_tag:
            description = desc_tag.get("content", "")
            
        keywords = []
        key_tag = soup.find("meta", attrs={"name": "keywords"})
        if key_tag:
            keywords = [k.strip() for k in key_tag.get("content", "").split(",")]
            
        for script_or_style in soup(["script", "style"]):
            script_or_style.decompose()
        raw_text = soup.get_text(separator=' ', strip=True)[:2000]

        new_links = []
        if depth < config.get("max_depth", 0):
            current_netloc = urlparse(url).netloc
            current_norm_domain = normalize_domain(current_netloc)
            
            for a in soup.find_all('a', href=True):
                link = clean_url(urljoin(url, a['href']))
                if not link:
                    continue
                
                link_parsed = urlparse(link)
                link_netloc = link_parsed.netloc
                link_norm_domain = normalize_domain(link_netloc)
                
                is_internal = (link_norm_domain == current_norm_domain)
                
                should_follow = False
                if depth == 0:
                    if is_internal and config.get('follow_internal', True):
                        should_follow = True
                    elif not is_internal and config.get('follow_external', True):
                        should_follow = True
                elif is_internal:
                    should_follow = True
                
                if should_follow:
                    crumb = link_netloc if not is_internal else link_parsed.path[:15] or "/"
                    new_links.append((link, depth + 1, breadcrumbs + [crumb]))

        soup.decompose()

        page_data = {
            "title": title.strip() if title else "",
            "url": url,
            "description": description.strip() if description else "",
            "keywords": keywords,
            "raw_text": raw_text,
            "depth": depth,
            "force_keep": (depth == 0)
        }
        
        return page_data, new_links
    except Exception:
        return None, []

def main():
    global pending_save_count
    sources_path = os.path.join(os.path.dirname(__file__), 'sources.json')
    if not os.path.exists(sources_path):
        print(f"Sources file not found: {sources_path}")
        return

    with open(sources_path, 'r') as f:
        sources = json.load(f)
    
    # Resumption
    processed_urls = load_progress()
    
    queue = deque()
    visited = set(processed_urls)
    in_queue = set()

    # Initialize Depth 0
    for feed_url in sources.get('feeds', []):
        print(f"Loading feed: {feed_url}")
        feed = feedparser.parse(feed_url)
        for entry in feed.entries:
            url = clean_url(entry.link)
            if url and url not in in_queue:
                queue.append((url, 0, [urlparse(url).netloc], {"target_depths": [0], "max_depth": 0}))
                in_queue.add(url)

    for site in sources.get('sites', []):
        url = clean_url(site['url'])
        if url and url not in in_queue:
            depth_config = site.get('crawl_depth', 0)
            target_depths = list(range(depth_config + 1)) if isinstance(depth_config, int) else depth_config
            max_depth = depth_config if isinstance(depth_config, int) else max(depth_config)
            
            queue.append((url, 0, [urlparse(url).netloc], {
                "target_depths": target_depths,
                "max_depth": max_depth,
                "follow_internal": site.get('follow_internal', True),
                "follow_external": site.get('follow_external', True)
            }))
            in_queue.add(url)

    # Filter queue items already processed
    queue = deque([item for item in queue if item[0] not in visited])
    in_queue = set(item[0] for item in queue)

    pbar = tqdm(total=len(processed_urls) + len(queue), initial=len(processed_urls), desc="Crawler", unit="site", dynamic_ncols=True)
    
    fetch_executor = ThreadPoolExecutor(max_workers=max_fetch_workers)
    llm_executor = ThreadPoolExecutor(max_workers=max_llm_workers)
    
    active_fetch_futures = []
    llm_futures = []
    
    def llm_done_callback(fut):
        global pending_save_count
        try:
            processed, keep = fut.result()
            if keep and processed:
                with results_lock:
                    all_results[processed['url']] = processed
                    pending_save_count += 1
        except Exception:
            pass
        finally:
            pbar.update(1)

    while queue or active_fetch_futures or llm_futures:
        # 1. Backpressure & Launch Fetches
        # Throttle fetching if LLM queue is getting too long (backpressure)
        llm_queue_limit = max_llm_workers * 4
        while queue and len(active_fetch_futures) < max_fetch_workers and len(llm_futures) < llm_queue_limit:
            url, depth, breadcrumbs, config = queue.popleft()
            future = fetch_executor.submit(fetch_and_discover, url, depth, breadcrumbs, config)
            active_fetch_futures.append((future, url, depth, config))
            pbar.set_description(f"Processing: {' > '.join(breadcrumbs[-3:])}")

        # 2. Check for completed fetches
        still_fetching = []
        for future, url, depth, config in active_fetch_futures:
            if future.done():
                visited.add(url)
                try:
                    page_data, new_links = future.result()
                    
                    if page_data and depth in config.get("target_depths", [0]):
                        llm_fut = llm_executor.submit(process_with_llm, page_data, force_keep=page_data['force_keep'])
                        llm_fut.add_done_callback(llm_done_callback)
                        llm_futures.append(llm_fut)
                    else:
                        pbar.update(1)

                    for l_url, l_depth, l_crumbs in new_links:
                        if l_url not in visited and l_url not in in_queue and len(visited) + len(queue) < max_total_visits:
                            queue.append((l_url, l_depth, l_crumbs, config))
                            in_queue.add(l_url)
                            pbar.total += 1
                            pbar.refresh()
                except Exception:
                    pbar.update(1)
                
                # Cleanup per URL
                gc.collect()
            else:
                still_fetching.append((future, url, depth, config))
        
        active_fetch_futures = still_fetching
        llm_futures = [f for f in llm_futures if not f.done()]

        # 3. Periodic Save
        if pending_save_count >= write_batch_size:
            save_progress()

        time.sleep(0.05)

    save_progress() # Final save
    pbar.close()

    # Final Index Generation
    print(f"Generating final index.json (limit: {max_index_size})...")
    sorted_results = sorted(all_results.values(), key=lambda x: x.get('quality_score', 0), reverse=True)
    final_results = sorted_results[:max_index_size]

    for item in final_results:
        item.pop('quality_score', None)
        item.pop('raw_text', None)

    output_path = os.path.join(os.path.dirname(__file__), '../frontend/index.json')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(final_results, f)
    
    print(f"\nCrawl complete. {len(final_results)} items saved to index.json")

if __name__ == "__main__":
    main()
