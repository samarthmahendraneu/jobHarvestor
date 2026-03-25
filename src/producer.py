import asyncio
import random
import os
import json
import logging
import logging_loki
from pyppeteer import launch
from dataclasses import dataclass
from typing import List, Dict, Optional

# Telemetry
from prometheus_client import start_http_server, Counter, Histogram, Gauge
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource

from src.broker import get_broker

from src.stealth import prepare_stealth_page
from src.rate_limiter import rate_limiter

# --- OBSERVABILITY SETUP ---
try:
    logging_loki.emitter.LokiEmitter.level_tag = "level"
    loki_handler = logging_loki.LokiHandler(
        url=os.getenv('LOKI_URL', "http://localhost:3100/loki/api/v1/push"),
        tags={"application": "jobharvestor-producer"},
        version="1",
    )
    logger = logging.getLogger("producer")
    logger.setLevel(logging.INFO)
    logger.addHandler(loki_handler)
    logger.addHandler(logging.StreamHandler())
except Exception as e:
    logger = logging.getLogger("producer")
    logger.addHandler(logging.StreamHandler())

# 2. Prometheus Metrics
JOBS_QUEUED = Counter('producer_jobs_queued_total', 'Total jobs queued to broker')
PAGES_PROCESSED = Counter('producer_pages_processed_total', 'Total listing pages processed')
PAGES_FAILED = Counter('producer_pages_failed_total', 'Total listing pages failed')
PAGE_SCRAPE_DURATION = Histogram('producer_page_scrape_duration_seconds', 'Time to scrape a single listing page', buckets=[1, 2, 5, 10, 20, 30, 60, 120])
ACTIVE_BROWSERS = Gauge('producer_active_browsers', 'Number of active headless browser instances')

# 3. Jaeger / OTel Tracing
resource = Resource(attributes={"service.name": "jobharvestor-producer"})
provider = TracerProvider(resource=resource)
otlp_endpoint = os.getenv('OTLP_ENDPOINT', "http://localhost:4318/v1/traces")
processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)


@dataclass
class ScraperPayload:
    url: str
    job_list_selector: str
    title_selector: str
    link_selector: str
    # Raw config metadata to pass down to consumer
    raw_config: dict


from urllib.parse import urljoin

async def get_inner_text(parent_element, selector: str, page) -> str:
    try:
        element = await parent_element.querySelector(selector)
        if element:
            text = await page.evaluate("el => el.innerText", element)
            return text.strip() if text else ""
    except Exception as e:
        logger.error(f"Failed to get text for selector '{selector}': {e}")
    return ""


async def get_link_href(parent_element, selector: str, page, base_url: str) -> str:
    try:
        href = await page.evaluate(f'''(el) => {{
            // Custom CSS selector search first
            if ("{selector}") {{
                const child = el.querySelector("{selector}");
                if (child && (child.getAttribute('href') || child.href)) {{
                    return child.getAttribute('href') || child.href;
                }}
            }}
            
            // Fallback: If the wrapper itself is the anchor tag
            if (el.tagName.toLowerCase() === 'a') {{
                return el.getAttribute('href') || el.href;
            }}
            
            // Ultimate fallback: find ANY anchor tag inside the wrapper
            const anyChild = el.querySelector("a");
            if (anyChild) return anyChild.getAttribute('href') || anyChild.href;
            
            return "";
        }}''', parent_element)

        if href:
            return urljoin(base_url, href.strip())
    except Exception as e:
        logger.error(f"Failed to get href for selector '{selector}': {e}")
    return ""


async def scrape_jobs_on_page(page, payload: ScraperPayload, broker) -> List[Dict[str, str]]:
    with tracer.start_as_current_span("scrape_jobs_on_page") as span:
        span.set_attribute("payload.url", payload.url)
        import time as _time
        _start = _time.time()
        try:
            await rate_limiter.wait(payload.url)
            await page.goto(payload.url, {
                'waitUntil': 'networkidle0',
                'timeout': 90000
            })
            await page.waitForSelector(payload.job_list_selector)

            jobs = []
            job_list_elements = await page.querySelectorAll(payload.job_list_selector)

            for job_el in job_list_elements:
                try:
                    title = await get_inner_text(job_el, payload.title_selector, page)
                    link = await get_link_href(job_el, payload.link_selector, page, payload.url)

                    # STRICT ENFORCEMENT: Without a hard link to navigate to, the consumer will completely crash.
                    if link:
                        jobs.append({"title": title, "link": link})
                        
                        # Package the URL and the Consumer Selectors together
                        consumer_payload = {
                            "url": link,
                            "job_id": payload.raw_config.get("job_id_selector"),
                            "title": payload.raw_config.get("job_title_selector"),
                            "location": payload.raw_config.get("location_selector"),
                            "department": payload.raw_config.get("department_selector"),
                            "summary": payload.raw_config.get("summary_selector"),
                            "long_description": payload.raw_config.get("long_description_selector"),
                            "date": payload.raw_config.get("date_selector")
                        }
                        
                        broker.produce("jobs", json.dumps(consumer_payload))
                        logger.info(f"[PRODUCER -> BROKER] {json.dumps(consumer_payload)}")
                        JOBS_QUEUED.inc()

                except Exception as e:
                    logger.error(f"Error extracting job element: {e}")

            PAGES_PROCESSED.inc()
            PAGE_SCRAPE_DURATION.observe(_time.time() - _start)
            span.set_attribute("jobs_found", len(jobs))
            return jobs

        except Exception as e:
            logger.error(f"Error scraping {payload.url}: {str(e)}")
            PAGES_FAILED.inc()
            span.record_exception(e)
            return []



async def scrape_batch(payloads: List[ScraperPayload]) -> None:
    with tracer.start_as_current_span("scrape_batch") as span:
        span.set_attribute("batch_size", len(payloads))
        broker = get_broker()
        browser = None
        try:
            logger.info("Initializing headless browser...")
            ACTIVE_BROWSERS.inc()
            chrome_path = os.getenv('CHROME_PATH', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
            browser = await launch(
                headless=True,
                executablePath=chrome_path,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',  '--disable-blink-features=AutomationControlled',]
            )

            results = []

            for payload in payloads:
                await asyncio.sleep(random.uniform(1, 3))
                page = await prepare_stealth_page(browser)
                page.setDefaultNavigationTimeout(90000)

                logger.info(f"Fetching {payload.url} ...")
                jobs = await scrape_jobs_on_page(page, payload, broker)
                logger.info(f"Found {len(jobs)} jobs on {payload.url}.")
                results.append((payload.url, jobs))

                await page.close()

        finally:
            ACTIVE_BROWSERS.dec()
            if browser:
                await browser.close()


async def start_harvest_for_company(config: dict):
    logger.info(f"Starting harvest for {config.get('company_name')}")
    base_url = config.get("base_url", "")
    
    queue = [
        ScraperPayload(
            url=f"{base_url}&page={i}" if "?" in base_url else f"{base_url}?page={i}",
            job_list_selector=config.get('job_list_selector', ''),
            title_selector=config.get('title_selector', ''),
            link_selector=config.get('link_selector', ''),
            raw_config=config
        )
        for i in range(10)
    ]

    batch_size = 5
    for i in range(0, len(queue), batch_size):
        batch = queue[i:i + batch_size]
        logger.info(f"Starting batch {i // batch_size + 1} with {len(batch)} tasks...")
        await scrape_batch(batch)
        logger.info(f"Batch {i // batch_size + 1} completed.")
        await asyncio.sleep(5) 


async def main():
    try:
        start_http_server(8000)
    except Exception:
        pass

    from src.broker import get_broker
    broker = get_broker()

    logger.info("Producer daemon started. Waiting for harvest-requests...")

    while True:
        try:
            messages = broker.consume("harvest-requests", batch_size=1)
            
            if not messages:
                await asyncio.sleep(3)
                continue

            for msg in messages:
                try:
                    config = json.loads(msg)
                    logger.info(f"Received harvest request for: {config.get('company_name')}")
                    await start_harvest_for_company(config)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid harvest request: {msg}")

        except Exception as e:
            logger.error(f"Producer loop error: {e}")
            await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Producer shutting down.")

