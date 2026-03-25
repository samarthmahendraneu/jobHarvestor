import os
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import uvicorn
from contextlib import asynccontextmanager

from src.Database.database import Database

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup the tables on startup and close on shutdown if necessary
    from prometheus_client import start_http_server
    try:
        start_http_server(8000)
    except Exception:
        pass # Already started
    
    db = Database()
    db.create_table_job_details()
    db.create_table_companies_config()
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

@app.post("/api/generate-selectors")
async def generate_selectors(req: SelectorRequest):
    try:
        from src.llm_extractor import extract_css_selectors
        import os
        if not os.getenv("OPENAI_API_KEY"):
            return {"error": "OPENAI_API_KEY is missing. Export it to your environment."}
        
        selectors = await extract_css_selectors(req.url)
        return {"status": "success", "selectors": selectors}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/logs")
async def get_logs():
    import urllib.request
    import urllib.parse
    import json
    try:
        # Query Loki for the last 50 logs of our app
        query = '{application=~"jobharvestor-consumer|jobharvestor-producer"}'
        url = f'http://loki:3100/loki/api/v1/query_range?query={urllib.parse.quote(query)}&limit=50'
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            lines = []
            for stream in data.get('data', {}).get('result', []):
                for val in stream.get('values', []):
                    lines.append(val[1])
            # Return reversed so newest is on top
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
        # Natively peel the Redis raw socket to check queue length without breaching the Interface
        if broker.__class__.__name__ == "RedisBroker":
            queue_len = broker.r.llen("jobs")
        else:
            queue_len = 0 # Kafka manages its own consumer lags
    except Exception:
        queue_len = 0
    
    # Query Jaeger for recent traces to show scraping speeds
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
    uvicorn.run("src.api:app", host="0.0.0.0", port=8080, reload=True)
