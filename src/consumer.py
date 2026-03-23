import asyncio
import random
import os
import json
import logging
import logging_loki
from pyppeteer import launch
from dataclasses import dataclass
from typing import Optional

# Telemetry
from prometheus_client import start_http_server, Counter
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource

# --- OBSERVABILITY SETUP ---
try:
    logging_loki.emitter.LokiEmitter.level_tag = "level"
    loki_handler = logging_loki.LokiHandler(
        url=os.getenv('LOKI_URL', "http://localhost:3100/loki/api/v1/push"),
        tags={"application": "jobharvestor-consumer"},
        version="1",
    )
    logger = logging.getLogger("consumer")
    logger.setLevel(logging.INFO)
    logger.addHandler(loki_handler)
    logger.addHandler(logging.StreamHandler())
except Exception as e:
    logger = logging.getLogger("consumer")
    logger.addHandler(logging.StreamHandler())

# 2. Prometheus Metrics
JOBS_INSERTED = Counter('consumer_jobs_inserted_total', 'Total job details successfully saved to DB')
JOBS_FAILED = Counter('consumer_jobs_failed_total', 'Total job detail pages failed to scrape')

# 3. Jaeger / OTel Tracing
resource = Resource(attributes={"service.name": "jobharvestor-consumer"})
provider = TracerProvider(resource=resource)
otlp_endpoint = os.getenv('OTLP_ENDPOINT', "http://localhost:4318/v1/traces")
processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)


@dataclass
class ScraperPayload:
    url: str
    job_id: Optional[str] = None
    title: Optional[str] = None
    location: Optional[str] = None
    department: Optional[str] = None
    summary: Optional[str] = None
    long_description: Optional[str] = None
    date: Optional[str] = None


async def get_inner_text(page, selector: str) -> str:
    if not selector:
        return ""
    try:
        element = await page.querySelector(selector)
        if element:
            text = await page.evaluate("el => el.innerText", element)
            return text.strip() if text else ""
    except Exception as e:
        logger.error(f"Failed to get text for selector '{selector}': {e}")
    return ""


async def scrape_job_details_on_page(page, payload: ScraperPayload):
    with tracer.start_as_current_span("scrape_job_details_on_page") as span:
        span.set_attribute("payload.url", payload.url)
        try:
            await asyncio.sleep(random.uniform(1, 3))
            await page.goto(payload.url, waitUntil='networkidle0', timeout=90000)

            job_id = await get_inner_text(page, payload.job_id)
            title = await get_inner_text(page, payload.title)
            location = await get_inner_text(page, payload.location)
            department = await get_inner_text(page, payload.department)
            summary = await get_inner_text(page, payload.summary)
            long_desc = await get_inner_text(page, payload.long_description)
            date_val = await get_inner_text(page, payload.date)

            from src.Database.database import Database
            db = Database()
            query = """
                INSERT INTO job_details(job_id, title, location, department, summary, long_description, date, url)
                VALUES(%s, %s, %s, %s, %s, %s, %s,%s)
                ON CONFLICT (job_id) DO NOTHING
            """
            params = (job_id, title, location, department, summary, long_desc, date_val, payload.url)
            db.insert_query(query, params)
            
            JOBS_INSERTED.inc()
            logger.info(f"Successfully processed and stored: {payload.url}")

        except Exception as e:
            logger.error(f"Error scraping job details from {payload.url}: {e}")
            span.record_exception(e)
            JOBS_FAILED.inc()


async def process_job_detail(payload, browser):
    page = None
    try:
        page = await browser.newPage()
        await scrape_job_details_on_page(page, payload)
    except Exception as e:
        logger.error(f"Error processing {payload.url}: {e}")
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass


async def scrape_batch(payloads):
    with tracer.start_as_current_span("consumer_scrape_batch") as span:
        import os
        browser = None
        try:
            chrome_path = os.getenv('CHROME_PATH', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
            browser = await launch(
                headless=True,
                executablePath=chrome_path,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
            )

            tasks = [process_job_detail(payload, browser) for payload in payloads]
            await asyncio.gather(*tasks)
        finally:
            if browser:
                await browser.close()


async def main():
    try:
        start_http_server(8001)
        logger.info("Prometheus metrics server started on port 8001")
    except Exception:
        pass # Already running

    from src.cache.Redis import Redis
    cache = Redis()

    queue = set(cache.get_list('jobs'))
    logger.info(f"Found {len(queue)} job URLs to scrape.")

    # Parse JSON payloads
    jobs = []
    for item in queue:
        try:
            data = json.loads(item)
            jobs.append(ScraperPayload(**data))
        except json.JSONDecodeError:
            # If there are lingering old string-only items in Redis from earlier tests
            logger.warning(f"Found non-JSON URL in queue: {item}")
            pass

    batch_size = 3
    for i in range(0, len(jobs), batch_size):
        batch = jobs[i : i + batch_size]
        logger.info(f"Scraping batch {i // batch_size + 1} with {len(batch)} items.")
        await scrape_batch(batch)
        await asyncio.sleep(10)

    logger.info("All done processing existing URLs!")


if __name__ == "__main__":
    while True:
        try:
            asyncio.run(main())
            import time
            time.sleep(10) # Loop forever acting as a true daemon consumer
        except Exception as e:
            logger.error(f"Consumer loop crashed: {e}")
            import time
            time.sleep(10)
