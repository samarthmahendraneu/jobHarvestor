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

class TestRequest(BaseModel):
    url: str

@app.get("/test", response_class=HTMLResponse)
async def test_page(request: Request):
    return templates.TemplateResponse(request=request, name="test.html")

def normalize_url(base_url: str, href: str) -> str:
    from urllib.parse import urljoin, urlparse
    if not href:
        return ""
    href = href.strip()
    if href.startswith(("http://", "https://")):
        return href
        
    joined = urljoin(base_url, href)
    
    # De-duplicate common path segments (e.g. /jobs/ + jobs/results -> /jobs/results)
    parsed = urlparse(joined)
    path_parts = parsed.path.split("/")
    clean_parts = []
    for i, part in enumerate(path_parts):
        if i > 0 and part and part == path_parts[i-1] and part in ("jobs", "results", "careers", "about"):
            continue
        clean_parts.append(part)
    
    new_path = "/".join(clean_parts)
    return f"{parsed.scheme}://{parsed.netloc}{new_path}"

@app.post("/api/test-extract")
async def test_extract(req: TestRequest):
    """Monolithic test endpoint (backwards compatible)."""
    try:
        from src.llm_extractor import extract_css_selectors
        logger.info(f"--- [API TEST-EXTRACT] Starting for {req.url} ---")
        selectors = await extract_css_selectors(req.url)
        
        from pyppeteer import launch
        chrome_path = os.getenv('CHROME_PATH', '/usr/bin/chromium')
        browser = await launch(headless=True, executablePath=chrome_path, args=['--no-sandbox'])
        page = await browser.newPage()
        await page.goto(req.url, {'waitUntil': 'networkidle2'})
        
        listings = await page.evaluate(f'''() => {{
            const results = [];
            const items = document.querySelectorAll("{selectors.get('job_list_selector', '')}");
            items.forEach(el => {{
                const titleEl = el.querySelector("{selectors.get('title_selector', '')}");
                const linkEl = el.querySelector("{selectors.get('link_selector', '')}");
                results.push({{
                    title: titleEl ? titleEl.innerText.trim() : el.innerText.trim(),
                    href: linkEl ? (linkEl.getAttribute('href') || linkEl.href) : ''
                }});
            }});
            return results.slice(0, 10);
        }}''')
        
        job_details = []
        for l in listings:
            l['href'] = normalize_url(req.url, l['href'])
            
        if listings:
            await page.goto(listings[0]['href'], {'waitUntil': 'networkidle2'})
            details = await page.evaluate(f'''(sels) => {{
                return {{
                    job_id: document.querySelector(sels.job_id_selector)?.innerText.trim() || '',
                    title: document.querySelector(sels.job_title_selector)?.innerText.trim() || '',
                    location: document.querySelector(sels.location_selector)?.innerText.trim() || '',
                    department: document.querySelector(sels.department_selector)?.innerText.trim() || '',
                    summary: document.querySelector(sels.summary_selector)?.innerText.trim() || '',
                    long_description: document.querySelector(sels.long_description_selector)?.innerText.trim() || '',
                    date: document.querySelector(sels.date_selector)?.innerText.trim() || ''
                }};
            }}''', selectors)
            details['url'] = listings[0]['href']
            job_details.append(details)

        await browser.close()
        return {"status": "success", "data": {"selectors": selectors, "listings": listings, "job_details": job_details}}
    except Exception as e:
        logger.error(f"Test extract failed: {e}")
        return {"status": "error", "error": str(e)}

@app.post("/api/test-listings")
async def test_listings(req: TestRequest):
    """Phase 12: Extract listing selectors and find all job links on page."""
    try:
        from src.llm_extractor import extract_listing_selectors
        logger.info(f"--- [API TEST-LISTINGS] Starting for {req.url} ---")
        selectors = await extract_listing_selectors(req.url)
        
        from pyppeteer import launch
        chrome_path = os.getenv('CHROME_PATH', '/usr/bin/chromium')
        browser = await launch(headless=True, executablePath=chrome_path, args=['--no-sandbox'])
        page = await browser.newPage()
        await page.goto(req.url, {'waitUntil': 'networkidle2'})
        
        listings = await page.evaluate(f'''(sels) => {{
            const results = [];
            const jls = sels.job_list_selector;
            if (!jls) return [];
            
            const items = document.querySelectorAll(jls);
            items.forEach(el => {{
                const titleEl = sels.title_selector ? el.querySelector(sels.title_selector) : null;
                const linkEl = sels.link_selector ? el.querySelector(sels.link_selector) : null;
                results.push({{
                    title: titleEl ? titleEl.innerText.trim() : el.innerText.trim(),
                    href: linkEl ? (linkEl.getAttribute('href') || linkEl.href) : ''
                }});
            }});
            return results;
        }}''', selectors)
        
        for l in listings:
            l['href'] = normalize_url(req.url, l['href'])
            
        await browser.close()
        return {"status": "success", "data": {"selectors": selectors, "listings": listings}}
    except Exception as e:
        logger.error(f"Test listings failed: {e}")
        return {"status": "error", "error": str(e)}

@app.post("/api/test-details")
async def test_details(req: TestRequest):
    """Phase 12: Extract detail selectors and scrape fields from one specific URL."""
    try:
        from src.llm_extractor import extract_details_selectors
        logger.info(f"--- [API TEST-DETAILS] Starting for {req.url} ---")
        selectors = await extract_details_selectors(req.url)
        
        from pyppeteer import launch
        chrome_path = os.getenv('CHROME_PATH', '/usr/bin/chromium')
        browser = await launch(headless=True, executablePath=chrome_path, args=['--no-sandbox'])
        page = await browser.newPage()
        await page.goto(req.url, {'waitUntil': 'networkidle2'})
        
        details = await page.evaluate(f'''(sels) => {{
            const getTxt = (sel) => {{
                if (!sel) return '';
                try {{
                    const el = document.querySelector(sel);
                    return el ? el.innerText.trim() : '';
                }} catch(e) {{ return ''; }}
            }};
            return {{
                job_id: getTxt(sels.job_id_selector),
                title: getTxt(sels.job_title_selector),
                location: getTxt(sels.location_selector),
                department: getTxt(sels.department_selector),
                summary: getTxt(sels.summary_selector),
                long_description: getTxt(sels.long_description_selector),
                date: getTxt(sels.date_selector)
            }};
        }}''', selectors)
        details['url'] = req.url
        
        await browser.close()
        return {"status": "success", "data": {"selectors": selectors, "job_details": [details]}}
    except Exception as e:
        logger.error(f"Test details failed: {e}")
        return {"status": "error", "error": str(e)}

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
