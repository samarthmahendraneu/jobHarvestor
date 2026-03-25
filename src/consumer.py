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
from prometheus_client import start_http_server, Counter, Histogram, Gauge
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource


from src.stealth import prepare_stealth_page
from src.rate_limiter import rate_limiter

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
BATCHES_PROCESSED = Counter('consumer_batches_processed_total', 'Total batches completed')
JOB_SCRAPE_DURATION = Histogram('consumer_job_scrape_duration_seconds', 'Time to scrape a single job detail page', buckets=[1, 2, 5, 10, 20, 30, 60, 120])
BATCH_DURATION = Histogram('consumer_batch_duration_seconds', 'Time to process an entire batch', buckets=[5, 10, 30, 60, 120, 300])
ACTIVE_BROWSERS = Gauge('consumer_active_browsers', 'Number of active headless browser instances')

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


async def scrape_job_details_on_page(page, payload: ScraperPayload, broker):
    with tracer.start_as_current_span("scrape_job_details_on_page") as span:
        span.set_attribute("payload.url", payload.url)
        import time as _time
        _start = _time.time()
        try:
            await rate_limiter.wait(payload.url)
            await asyncio.sleep(random.uniform(1, 3))
            await page.goto(payload.url, {'waitUntil': 'networkidle0', 'timeout': 90000})
            
            # Massive stabilization wait: SPAs take heavy milliseconds to mount elements!
            await asyncio.sleep(3.5)
            
            job_id = await get_inner_text(page, payload.job_id)
            title = await get_inner_text(page, payload.title)
            location = await get_inner_text(page, payload.location)
            department = await get_inner_text(page, payload.department)
            summary = await get_inner_text(page, payload.summary)
            long_desc = await get_inner_text(page, payload.long_description)
            date_val = await get_inner_text(page, payload.date)

            logger.info(f"[SCRAPER] Extracted for {payload.url}: ID='{job_id}', Title='{title}', Loc='{location}'")

            from src.Database.database import Database
            db = Database()
            query = """
                INSERT INTO job_details(job_id, title, location, department, summary, long_description, date, url)
                VALUES(%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (url) DO NOTHING
            """
            params = (job_id, title, location, department, summary, long_desc, date_val, payload.url)
            
            logger.info(f"[CONSUMER -> POSTGRES] Attempting insert for URL: {payload.url}")
            db.insert_query(query, params)
            
            JOBS_INSERTED.inc()
            JOB_SCRAPE_DURATION.observe(_time.time() - _start)
            logger.info(f"Successfully processed and stored: {payload.url}")

        except Exception as e:
            logger.error(f"Error scraping job details from {payload.url}: {e}")
            span.record_exception(e)
            JOBS_FAILED.inc()
            
            # Publish to dead-letter queue for later inspection/retry
            import json
            dlq_payload = {
                "url": payload.url,
                "error": str(e),
                "job_id": payload.job_id,
                "title": payload.title,
            }
            try:
                broker.produce("jobs-dlq", json.dumps(dlq_payload))
                logger.info(f"[DLQ] Published failed job to jobs-dlq: {payload.url}")
            except Exception as dlq_err:
                logger.error(f"Failed to publish to DLQ: {dlq_err}")


async def process_job_detail(payload, browser, broker):
    page = None
    try:
        page = await prepare_stealth_page(browser)
        await scrape_job_details_on_page(page, payload, broker)
    except Exception as e:
        logger.error(f"Error processing {payload.url}: {e}")
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass


async def scrape_batch(payloads, broker):
    with tracer.start_as_current_span("consumer_scrape_batch") as span:
        import os
        import time as _time
        _batch_start = _time.time()
        browser = None
        try:
            chrome_path = os.getenv('CHROME_PATH', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
            ACTIVE_BROWSERS.inc()
            browser = await launch(
                headless=True,
                executablePath=chrome_path,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled']
            )

            tasks = [process_job_detail(payload, browser, broker) for payload in payloads]
            await asyncio.gather(*tasks)
        finally:
            ACTIVE_BROWSERS.dec()
            BATCHES_PROCESSED.inc()
            BATCH_DURATION.observe(_time.time() - _batch_start)
            if browser:
                await browser.close()


async def main():
    try:
        start_http_server(8001)
        logger.info("Prometheus metrics server started on port 8001")
    except Exception:
        pass # Already running

    from src.broker import get_broker
    broker = get_broker()

    logger.info("Connecting to core queue streaming engine... Subscribing to telemetry topics...")

    # Infinite daemon event loop running natively inside the async cluster
    while True:
        try:
            messages = broker.consume("jobs", batch_size=3)
            
            if not messages:
                await asyncio.sleep(5)
                continue

            logger.info(f"Pulled {len(messages)} jobs from message broker.")

            jobs = []
            for item in messages:
                try:
                    data = json.loads(item)
                    jobs.append(ScraperPayload(**data))
                except json.JSONDecodeError:
                    logger.warning(f"Found non-JSON URL in queue: {item}")
                    pass

            if jobs:
                logger.info(f"Scraping active queue micro-batch of {len(jobs)} items.")
                await scrape_batch(jobs, broker)

            await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"Consumer loop iteration crashed: {e}")
            await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Termination signal received. Shutting down Consumer.")
