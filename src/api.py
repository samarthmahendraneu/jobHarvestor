import os
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import uvicorn
from contextlib import asynccontextmanager

from src.Database.database import Database

# --- We will import start_harvest from producer once we refactor it ---
from src.producer import start_harvest_for_company

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup the tables on startup and close on shutdown if necessary
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
    
    # Trigger the background task without blocking the API
    asyncio.create_task(start_harvest_for_company(config))
    return {"status": "started", "message": f"Harvest started in background for {config['company_name']}"}

if __name__ == "__main__":
    uvicorn.run("src.api:app", host="0.0.0.0", port=8080, reload=True)
