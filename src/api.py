import os
import asyncio
import logging
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import uvicorn
from contextlib import asynccontextmanager

from src.Database.database import Database

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("api-dashboard")

@asynccontextmanager
async def lifespan(app: FastAPI):
    from prometheus_client import start_http_server
    try:
        start_http_server(8000)
    except Exception:
        pass  # Already started
    yield

app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="src/templates")

class CompanyConfig(BaseModel):
    company_name: str
    base_url: str
    job_list_selector: str = ""
    title_selector: str = ""
    link_selector: str = ""
    job_id_selector: str = ""
    job_title_selector: str = ""
    location_selector: str = ""
    department_selector: str = ""
    summary_selector: str = ""
    long_description_selector: str = ""
    date_selector: str = ""

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/api/companies")
async def get_companies():
    db = Database()
    query = "SELECT * FROM companies_config"
    rows = db.execute_query(query)
    # Convert DB rows to dict list
    companies = []
    for r in rows:
        companies.append(dict(r))
    return companies

@app.post("/api/companies")
async def create_company(config: CompanyConfig):
    db = Database()
    query = """
        INSERT INTO companies_config (
            company_name, base_url, job_list_selector, title_selector, link_selector,
            job_id_selector, job_title_selector, location_selector, department_selector,
            summary_selector, long_description_selector, date_selector
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    params = (
        config.company_name, config.base_url, config.job_list_selector,
        config.title_selector, config.link_selector, config.job_id_selector,
        config.job_title_selector, config.location_selector, config.department_selector,
        config.summary_selector, config.long_description_selector, config.date_selector
    )
    db.insert_query(query, params)
    return {"status": "success"}

@app.post("/api/harvest/{company_id}")
async def run_harvest(company_id: int):
    db = Database()
    query = "SELECT * FROM companies_config WHERE id = %s"
    row = db.execute_query_with_params_and_fetch_one(query, (company_id,))
    if not row:
        return {"error": "Company not found", "status": "failed"}
    
    config = dict(row)
    
    # Publish to harvest-requests topic — producer pods pick this up independently
    import json
    from src.broker import get_broker
    broker = get_broker()
    broker.produce("harvest-requests", json.dumps(config, default=str))
    return {"status": "started", "message": f"Harvest request queued for {config['company_name']}"}

class SelectorRequest(BaseModel):
    url: str

@app.get("/test", response_class=HTMLResponse)
async def test_page(request: Request):
    return templates.TemplateResponse(request=request, name="test.html")

@app.post("/api/generate-selectors")
async def generate_selectors(req: SelectorRequest):
    try:
        from src.llm_extractor import extract_css_selectors
        selectors = await extract_css_selectors(req.url)
        return {"status": "success", "selectors": selectors}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/test-extract")
async def test_extract(req: SelectorRequest):
    import asyncio
    from urllib.parse import urljoin, urlparse

    def normalize_url(current_url: str, href: str) -> str:
        if not href:
            return ""
        href = href.strip()
        if href.startswith(("http://", "https://")):
            return href
        if href.startswith("//"):
            parsed = urlparse(current_url)
            return f"{parsed.scheme}:{href}"
        
        joined = urljoin(current_url, href)
        
        # De-duplicate common path segments that often get doubled up by urljoin
        # e.g. .../jobs/ + jobs/results -> .../jobs/jobs/results
        parsed_joined = urlparse(joined)
        path_parts = parsed_joined.path.split("/")
        clean_parts = []
        for i, part in enumerate(path_parts):
            if i > 0 and part and part == path_parts[i-1] and part in ("jobs", "results", "careers", "about"):
                continue
            clean_parts.append(part)
        
        new_path = "/".join(clean_parts)
        return f"{parsed_joined.scheme}://{parsed_joined.netloc}{new_path}"

    try:
        async def run_test():
            from src.llm_extractor import extract_css_selectors
            from src.stealth import prepare_stealth_page
            from pyppeteer import launch

            browser = await launch(
                headless=True,
                executablePath=os.getenv('CHROME_PATH', '/usr/bin/chromium'),
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-blink-features=AutomationControlled'
                ]
            )

            try:
                # 1. Get Selectors
                logger.info(f"--- [API TEST-EXTRACT] Starting for {req.url} ---")
                selectors = await extract_css_selectors(req.url)
                jls = selectors.get("job_list_selector", "")
                title_sel = selectors.get("title_selector", "")
                link_sel = selectors.get("link_selector", "")

                if not jls:
                    logger.warning("No job_list_selector found in results.")
                    return {"error": "No job_list_selector found", "selectors": selectors}

                # 2. Load list page
                logger.info(f"Loading listing page in stealth mode: {req.url}")
                page = await prepare_stealth_page(browser)
                await page.goto(req.url, {'waitUntil': 'networkidle0', 'timeout': 60000})
                await asyncio.sleep(3)
                
                base_page_url = page.url 
                logger.info(f"List page loaded. Final URL: {base_page_url}")

                # 3. Get first job
                logger.info("Extracting first job data from list...")
                job = await page.evaluate("""(jls, title_sel, link_sel) => {
                    const el = document.querySelector(jls);
                    if (!el) return null;
                    const titleEl = title_sel ? (el.querySelector(title_sel) || el) : el;
                    const linkEl = link_sel ? (el.querySelector(link_sel) || el.querySelector('a')) : el.querySelector('a');
                    return {
                        title: titleEl ? titleEl.innerText.trim() : el.innerText.trim(),
                        href: linkEl ? (linkEl.getAttribute("href") || linkEl.href || "") : ""
                    };
                }""", jls, title_sel, link_sel)

                if not job or not job.get("href"):
                    logger.error("Failed to find even one job/link on the list page.")
                    return {"error": "No jobs found with those selectors", "selectors": selectors}

                job_url = normalize_url(base_page_url, job["href"])
                logger.info(f"Targeting detail page: {job_url}")

                # 4. Scrape details
                detail_page = await prepare_stealth_page(browser)
                logger.info("Navigating to detail page...")
                await detail_page.goto(job_url, {'waitUntil': 'networkidle0', 'timeout': 60000})
                await asyncio.sleep(3)

                detail_sels = {
                    "job_id": selectors.get("job_id_selector", ""),
                    "title": selectors.get("job_title_selector", ""),
                    "location": selectors.get("location_selector", ""),
                    "department": selectors.get("department_selector", ""),
                    "summary": selectors.get("summary_selector", ""),
                    "long_description": selectors.get("long_description_selector", ""),
                    "date": selectors.get("date_selector", ""),
                }

                detail = {"url": job_url, "listing_title": job["title"]}
                logger.info(f"Applying detail selectors: {detail_sels}")
                for field, sel in detail_sels.items():
                    if not sel:
                        detail[field] = ""
                        continue
                    try:
                        val = await detail_page.evaluate("""(selector) => {
                            const el = document.querySelector(selector);
                            return el ? el.innerText.trim() : "";
                        }""", sel)
                        detail[field] = val
                    except:
                        detail[field] = ""
                
                logger.info(f"Extraction complete for: {job_url}")
                await detail_page.close()
                await page.close()

                return {
                    "selectors": selectors,
                    "listings": [{"title": job["title"], "href": job["href"]}],
                    "job_details": [detail]
                }

            finally:
                await browser.close()

        result = await asyncio.wait_for(run_test(), timeout=180)
        return {"status": "success", "data": result}

    except asyncio.TimeoutError:
        return {"error": "Timed out after 3 minutes."}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/logs")
async def get_logs():
    import urllib.request
    import urllib.parse
    import json
    try:
        query = '{application=~"jobharvestor-consumer|jobharvestor-producer"}'
        url = f'http://loki:3100/loki/api/v1/query_range?query={urllib.parse.quote(query)}&limit=50'
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            lines = []
            for stream in data.get('data', {}).get('result', []):
                for val in stream.get('values', []):
                    lines.append(val[1])
            return {"logs": lines[::-1][:50]}
    except Exception as e:
        return {"logs": [f"Waiting for logs... (or Loki not reachable: {e})"]}

@app.get("/api/stats")
async def get_stats():
    from src.broker import get_broker
    import urllib.request
    import json
    
    try:
        broker = get_broker()
        if broker.__class__.__name__ == "RedisBroker":
            queue_len = broker.r.llen("jobs")
        else:
            queue_len = 0
    except Exception:
        queue_len = 0
    
    traces = []
    try:
        url = "http://jaeger:16686/api/traces?service=jobharvestor-consumer&limit=5"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=2) as response:
            data = json.loads(response.read().decode())
            for t in data.get("data", []):
                dur = max([span.get("duration", 0) for span in t.get("spans", [{'duration':0}])]) / 1000.0
                traces.append({"trace_id": t["traceID"], "latency_ms": dur, "process": "Consumer"})
    except Exception:
        pass

    return {"queue_length": queue_len, "recent_traces": traces}

if __name__ == "__main__":
    uvicorn.run("src.api:app", host="0.0.0.0", port=8090, reload=True)
