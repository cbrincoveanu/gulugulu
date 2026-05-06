import requests
from bs4 import BeautifulSoup
import feedparser
import json
import os
import re
from urllib.parse import urljoin, urlparse
from dotenv import load_dotenv
from openai import OpenAI
from concurrent.futures import ThreadPoolExecutor, as_completed

load_dotenv()

# Setup LLM
llm_enabled = os.getenv("OPENAI_API_BASE") is not None
max_workers = int(os.getenv("MAX_CONCURRENT_LLM", 5))

if llm_enabled:
    print(f"LLM enabled using base: {os.getenv('OPENAI_API_BASE')}")
    client = OpenAI(
        base_url=os.getenv("OPENAI_API_BASE"),
        api_key=os.getenv("OPENAI_API_KEY", "no-key")
    )
    llm_model = os.getenv("LLM_MODEL", "llama3")
else:
    print("LLM disabled (no OPENAI_API_BASE found in .env)")

DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

def get_page_data(url):
    try:
        response = requests.get(url, headers=DEFAULT_HEADERS, timeout=10, stream=True)
        response.raise_for_status()
        
        content_type = response.headers.get('Content-Type', '').lower()
        if 'text/html' not in content_type:
            print(f"Skipping non-HTML content: {url} ({content_type})")
            return None

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
            
        return {
            "title": title.strip() if title else "",
            "url": url,
            "description": description.strip() if description else "",
            "keywords": keywords,
            "raw_text": raw_text
        }
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

def process_with_llm(data, force_keep=False):
    if not llm_enabled:
        return data, True

    print(f"LLM processing: {data['url']} (force_keep: {force_keep})")
    
    prompt = f"""Analyze the following website data and decide if it belongs in a search engine for the 'Old Web' (niche, personal, unoptimized, high signal, non-corporate).

Data:
URL: {data['url']}
Title: {data['title']}
Meta Description: {data['description']}
Text Snippet: {data['raw_text']}

Respond ONLY with a JSON object in this format:
{{
  "keep": true,
  "title": "Cleaned Title",
  "description": "High-quality description",
  "keywords": ["keyword1", "keyword2", "keyword3"]
}}"""

    try:
        response = client.chat.completions.create(
            model=llm_model,
            messages=[
                {"role": "system", "content": "You are a curated web directory editor. Output valid JSON only."},
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
            print(f"LLM rejected: {data['url']}")
            return None, False
        
        return {
            "title": parsed.get("title", data["title"]),
            "url": data["url"],
            "description": parsed.get("description", data["description"]),
            "keywords": parsed.get("keywords", data["keywords"])
        }, True
    except Exception as e:
        print(f"LLM error for {data['url']}, falling back: {e}")
        return data, True

def crawl_site(site_url, depth_config, executor, follow_internal=True, follow_external=True):
    if isinstance(depth_config, int):
        target_depths = list(range(depth_config + 1))
        max_depth = depth_config
    else:
        target_depths = depth_config
        max_depth = max(depth_config)

    futures = []
    visited = set()
    to_visit = [(site_url, 0)]
    in_queue = {site_url}
    
    while to_visit:
        url, current_depth = to_visit.pop(0)
        visited.add(url)
        
        page_data = get_page_data(url)
        
        if page_data:
            if current_depth in target_depths:
                print(f"Submitting for LLM (depth {current_depth}): {url}")
                futures.append(executor.submit(process_with_llm, page_data, force_keep=(current_depth == 0)))
            
            if current_depth < max_depth:
                try:
                    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=10)
                    soup = BeautifulSoup(response.text, 'html.parser')
                    current_domain = urlparse(url).netloc
                    
                    for a in soup.find_all('a', href=True):
                        link = urljoin(url, a['href'])
                        parsed_link = urlparse(link)
                        clean_link = f"{parsed_link.scheme}://{parsed_link.netloc}{parsed_link.path}"
                        if parsed_link.query:
                            clean_link += f"?{parsed_link.query}"
                        
                        if clean_link in visited or clean_link in in_queue:
                            continue
                            
                        link_domain = urlparse(clean_link).netloc
                        is_internal = (link_domain == current_domain)
                        
                        # Rule: 
                        # 1. If at depth 0, we check the follow_internal/follow_external flags
                        # 2. If at depth > 0, we only follow links on the same domain (internal)
                        should_follow = False
                        if current_depth == 0:
                            if is_internal and follow_internal:
                                should_follow = True
                            elif not is_internal and follow_external:
                                should_follow = True
                        elif is_internal:
                            should_follow = True
                            
                        if should_follow:
                            to_visit.append((clean_link, current_depth + 1))
                            in_queue.add(clean_link)
                except Exception as e:
                    print(f"Error finding links on {url}: {e}")
    return futures

def main():
    sources_path = os.path.join(os.path.dirname(__file__), 'sources.json')
    if not os.path.exists(sources_path):
        print(f"Sources file not found: {sources_path}")
        return

    with open(sources_path, 'r') as f:
        sources = json.load(f)
    
    all_futures = []
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for feed_url in sources.get('feeds', []):
            print(f"Processing feed: {feed_url}")
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                data = {
                    "title": entry.title,
                    "url": entry.link,
                    "description": entry.get('summary', '') or entry.get('description', ''),
                    "keywords": [],
                    "raw_text": entry.get('summary', '') or entry.get('description', '')
                }
                all_futures.append(executor.submit(process_with_llm, data))
                
        for site in sources.get('sites', []):
            url = site.get('url')
            depth = site.get('crawl_depth', 0)
            f_internal = site.get('follow_internal', True)
            f_external = site.get('follow_external', True)
            all_futures.extend(crawl_site(url, depth, executor, f_internal, f_external))

        all_results = []
        print(f"Waiting for {len(all_futures)} LLM tasks to complete...")
        for future in as_completed(all_futures):
            processed, keep = future.result()
            if keep and processed:
                all_results.append(processed)

    unique_results = []
    seen_urls = set()
    for item in sorted(all_results, key=lambda x: x['url']):
        if item['url'] not in seen_urls:
            unique_results.append(item)
            seen_urls.add(item['url'])

    output_path = os.path.join(os.path.dirname(__file__), '../frontend/index.json')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(unique_results, f)
    
    print(f"Crawl complete. {len(unique_results)} items saved to index.json")

if __name__ == "__main__":
    main()
