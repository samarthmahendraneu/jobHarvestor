import asyncio
import os
import json
import logging
from pyppeteer import launch
from urllib.parse import urljoin, urlparse

from src.stealth import prepare_stealth_page
from src.broker import get_broker

from prometheus_client import start_http_server, Counter, Histogram, Gauge

# --- OBSERVABILITY SETUP ---
# 1. Prometheus Metrics
LISTINGS_FOUND = Counter('discovery_listings_found_total', 'Total job links discovered')
PAGES_PROCESSED = Counter('discovery_pages_processed_total', 'Total listing pages successfully scraped')
PAGES_FAILED = Counter('discovery_pages_failed_total', 'Total listing pages failed')
SCRAPE_DURATION = Histogram('discovery_scrape_duration_seconds', 'Time to scrape a listing page', buckets=[1, 2, 5, 10, 20, 30, 60])
ACTIVE_BROWSERS = Gauge('discovery_active_browsers', 'Number of active discovery browser instances')
logger = logging.getLogger("discovery-consumer")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

def normalize_url(base_url: str, href: str) -> str:
    if not href:
        return ""
    href = href.strip()
    if href.startswith(("http://", "https://")):
        return href
        
    joined = urljoin(base_url, href)
    
    # De-duplicate common path segments
    parsed = urlparse(joined)
    path_parts = parsed.path.split("/")
    clean_parts = []
    for i, part in enumerate(path_parts):
        if i > 0 and part and part == path_parts[i-1] and part in ("jobs", "results", "careers", "about"):
            continue
        clean_parts.append(part)
    
    new_path = "/".join(clean_parts)
    return f"{parsed.scheme}://{parsed.netloc}{new_path}"

async def run_discovery(config_data, broker):
    """
    Scrapes a listings page and pushes individual job links to the 'jobs' topic.
    """
    base_url = config_data.get('base_url')
    wrapper_selector = config_data.get('job_list_selector')
    title_subselector = config_data.get('title_selector')
    link_subselector = config_data.get('link_selector')
    
    if not base_url or not wrapper_selector:
        logger.error(f"Missing essential discovery config for {config_data.get('company_name')}")
        return

    logger.info(f"--- [DISCOVERY] Starting for {config_data.get('company_name')} at {base_url} ---")
    
    import time as _time
    _start = _time.time()
    browser = None
    try:
        chrome_path = os.getenv('CHROME_PATH', '/usr/bin/chromium')
        ACTIVE_BROWSERS.inc()
        browser = await launch(
            headless=True,
            executablePath=chrome_path,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled']
        )
        page = await prepare_stealth_page(browser)
        
        await page.goto(base_url, {'waitUntil': 'networkidle2', 'timeout': 60000})
        await asyncio.sleep(2) # Give it a moment for any animations/renders
        
        job_links = await page.evaluate(f'''(wrapper, titleSel, linkSel) => {{
            const items = document.querySelectorAll(wrapper);
            const results = [];
            items.forEach(item => {{
                const titleEl = titleSel ? item.querySelector(titleSel) : null;
                const linkEl = linkSel ? item.querySelector(linkSel) : item;
                
                if (linkEl && (linkEl.tagName === 'A' || linkEl.querySelector('a'))) {{
                    const finalLink = linkEl.tagName === 'A' ? linkEl : linkEl.querySelector('a');
                    results.push({{
                        title: titleEl ? titleEl.innerText.trim() : '',
                        href: finalLink.getAttribute('href')
                    }});
                }}
            }});
            return results;
        }}''', wrapper_selector, title_subselector, link_subselector)
        
        logger.info(f"[DISCOVERY] Found {len(job_links)} listings.")
        LISTINGS_FOUND.inc(len(job_links))
        PAGES_PROCESSED.inc()
        SCRAPE_DURATION.observe(_time.time() - _start)
        
        for link in job_links:
            full_url = normalize_url(base_url, link['href'])
            if not full_url:
                continue
                
            # Construct payload for the detail consumer
            job_payload = {
                "url": full_url,
                "job_id": config_data.get('job_id_selector'),
                "title": config_data.get('job_title_selector'),
                "location": config_data.get('location_selector'),
                "department": config_data.get('department_selector'),
                "summary": config_data.get('summary_selector'),
                "long_description": config_data.get('long_description_selector'),
                "date": config_data.get('date_selector')
            }
            
            broker.produce("jobs", json.dumps(job_payload))
            logger.info(f"[DISCOVERY -> JOBS] Produced: {full_url}")
            
    except Exception as e:
        logger.error(f"Discovery failed for {base_url}: {e}")
        PAGES_FAILED.inc()
    finally:
        ACTIVE_BROWSERS.dec()
        if browser:
            await browser.close()

async def main():
    try:
        start_http_server(8002)
        logger.info("Prometheus metrics server started on port 8002")
    except Exception:
        pass

    broker = get_broker()
    logger.info("Discovery Consumer started. Subscribing to 'harvest-requests'...")
    
    while True:
        try:
            logger.info("Polling harvest-requests...")
            messages = broker.consume("harvest-requests", batch_size=1)
            if not messages:
                logger.info("No messages found in this poll.")
                await asyncio.sleep(5)
                continue
                
            for msg in messages:
                try:
                    config = json.loads(msg)
                    await run_discovery(config, broker)
                except Exception as e:
                    logger.error(f"Failed to process harvest request: {e}")
                    
        except Exception as e:
            logger.error(f"Discovery loop error: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
