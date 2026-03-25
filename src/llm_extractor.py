import os
import re
import logging
from pyppeteer import launch
from pydantic import BaseModel
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# Retaining your local API Key!
os.environ["OPENAI_API_KEY"] = ""

class ListingSchema(BaseModel):
    job_list_selector: str
    title_selector: str
    link_selector: str

class DetailsSchema(BaseModel):
    job_id_selector: str
    job_title_selector: str
    location_selector: str
    department_selector: str
    summary_selector: str
    long_description_selector: str
    date_selector: str

async def get_condensed_html(page) -> str:
    html = await page.evaluate('''() => {
        document.querySelectorAll('script, style, svg, img, noscript, nav, footer, iframe, header, form, path, meta, link').forEach(el => el.remove());
        return document.body ? document.body.innerHTML : document.documentElement.innerHTML;
    }''')
    html = re.sub(r'<!--(.*?)-->', '', html, flags=re.DOTALL)
    html = re.sub(r'\s+', ' ', html)
    html = re.sub(r'>\s+<', '><', html)
    return html[:150000]

async def extract_listing_selectors(url: str, browser=None) -> dict:
    logger.info(f"--- [LLM LISTINGS] Starting for: {url} ---")
    if not os.getenv("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY is missing!")
        raise ValueError("OPENAI_API_KEY is missing! Set it before auto-extracting.")

    own_browser = False
    if browser is None:
        chrome_path = os.getenv('CHROME_PATH', '/usr/bin/chromium')
        logger.info(f"Launching browser with: {chrome_path}")
        browser = await launch(headless=True, executablePath=chrome_path, args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'])
        own_browser = True
    
    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    try:
        page = await browser.newPage()
        await page.goto(url, {'waitUntil': 'networkidle2', 'timeout': 60000})
        list_html = await get_condensed_html(page)
        logger.info(f"Listing HTML condensed to {len(list_html)} chars.")
        
        list_msgs = [
            {"role": "system", "content": "You are a CSS selector expert. Analyze this career listings HTML. Extract `job_list_selector` (the repeating wrapper for a single job), `title_selector` (the job title inside the wrapper), and `link_selector` (the <a> link inside the wrapper)."},
            {"role": "user", "content": f"URL: {url}\n\nHTML:\n{list_html}"}
        ]
        
        listing_info = {}
        for attempt in range(3):
            logger.info(f"Requesting Listing selectors from GPT-4o (Attempt {attempt+1}/3)...")
            list_res = await client.beta.chat.completions.parse(model="gpt-4o", temperature=0.1, messages=list_msgs, response_format=ListingSchema)
            listing_info = list_res.choices[0].message.parsed.model_dump()
            logger.info(f"GPT suggested Listing selectors: {listing_info}")
            
            is_valid = await page.evaluate(f'''() => {{
                try {{
                    const wrapper = document.querySelector("{listing_info['job_list_selector']}");
                    if (!wrapper) return false;
                    const title = wrapper.querySelector("{listing_info['title_selector']}");
                    return !!title;
                }} catch(e) {{ return false; }}
            }}''')
            
            if is_valid:
                logger.info("Listing selectors VALIDATED on live page.")
                break
            
            if attempt < 2:
                logger.warning(f"Validation FAILED for {listing_info['job_list_selector']}. Asking GPT to self-correct...")
                list_msgs.append({"role": "assistant", "content": list_res.choices[0].message.content or "{}"})
                list_msgs.append({"role": "user", "content": f"Validation failed! document.querySelector('{listing_info['job_list_selector']}') returned null. Please find a more reliable, broader semantic CSS selector that physically exists in this DOM."})
            else:
                logger.error("Listing validation failed after 3 attempts. Proceeding with best guess.")
        
        await page.close()
        return listing_info
    finally:
        if own_browser:
            await browser.close()
        logger.info("--- [LLM LISTINGS] Finished ---")

async def extract_details_selectors(url: str, browser=None) -> dict:
    logger.info(f"--- [LLM DETAILS] Starting for: {url} ---")
    if not os.getenv("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY is missing!")
        raise ValueError("OPENAI_API_KEY is missing! Set it before auto-extracting.")

    own_browser = False
    if browser is None:
        chrome_path = os.getenv('CHROME_PATH', '/usr/bin/chromium')
        logger.info(f"Launching browser with: {chrome_path}")
        browser = await launch(headless=True, executablePath=chrome_path, args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'])
        own_browser = True

    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    try:
        page = await browser.newPage()
        await page.goto(url, {'waitUntil': 'networkidle2', 'timeout': 60000})
        details_html = await get_condensed_html(page)
        logger.info(f"Details HTML condensed to {len(details_html)} chars.")

        details_msgs = [
            {"role": "system", "content": "You are a CSS selector expert. Analyze this Job Details page HTML. Extract robust CSS selectors to locate the ID, title, location, department, summary, description, and date. If a field isn't present, return an empty string."},
            {"role": "user", "content": f"URL: {url}\n\nHTML:\n{details_html}"}
        ]
        
        details_info = {}
        for attempt in range(3):
            logger.info(f"Requesting Details selectors from GPT-4o (Attempt {attempt+1}/3)...")
            det_res = await client.beta.chat.completions.parse(model="gpt-4o", temperature=0.1, messages=details_msgs, response_format=DetailsSchema)
            details_info = det_res.choices[0].message.parsed.model_dump()
            logger.info(f"GPT suggested Details selectors: {details_info}")
            
            title_exists = await page.evaluate(f'''() => {{
                try {{ return !!document.querySelector("{details_info['job_title_selector']}"); }} catch(e) {{ return false; }}
            }}''')
            
            if title_exists:
                logger.info("Details title selector VALIDATED on live page.")
                break
            
            if attempt < 2:
                logger.warning(f"Validation FAILED for {details_info['job_title_selector']}. Asking GPT to self-correct...")
                details_msgs.append({"role": "assistant", "content": det_res.choices[0].message.content or "{}"})
                details_msgs.append({"role": "user", "content": f"Validation failed! document.querySelector('{details_info['job_title_selector']}') didn't find the title. Look closely at the HTML and try a more robust semantic selector."})
            else:
                logger.error("Details validation failed after 3 attempts.")
        
        await page.close()
        return details_info
    finally:
        if own_browser:
            await browser.close()
        logger.info("--- [LLM DETAILS] Finished ---")

async def extract_css_selectors(url: str) -> dict:
    """Wrapper that autonomously finds both listing and detail selectors."""
    logger.info(f"--- [LLM CONSOLIDATED] Starting for: {url} ---")
    if not os.getenv("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY is missing!")
        raise ValueError("OPENAI_API_KEY is missing! Set it before auto-extracting.")

    chrome_path = os.getenv('CHROME_PATH', '/usr/bin/chromium')
    logger.info(f"Launching browser with: {chrome_path}")
    browser = await launch(headless=True, executablePath=chrome_path, args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'])
    
    final_selectors = {}
    try:
        # Step 1: Extract Listing Selectors
        listing_info = await extract_listing_selectors(url, browser=browser)
        final_selectors.update(listing_info)
        
        # Bridge: Find a link to get details
        first_job_href = None
        if listing_info.get('job_list_selector') and listing_info.get('link_selector'):
            page = await browser.newPage()
            await page.goto(url, {'waitUntil': 'networkidle2', 'timeout': 60000})
            first_job_href = await page.evaluate(f'''() => {{
                const wrapper = document.querySelector("{listing_info['job_list_selector']}");
                if (!wrapper) return null;
                if (wrapper.tagName.toLowerCase() === 'a' && wrapper.href) return wrapper.getAttribute('href') || wrapper.href;
                const link = wrapper.querySelector("{listing_info['link_selector']}");
                return link ? (link.getAttribute('href') || link.href) : null;
            }}''')
            await page.close()

        if first_job_href:
            from urllib.parse import urljoin
            first_job_href = urljoin(url, first_job_href)
            logger.info(f"Found first job link: {first_job_href}. Navigating for Details extraction...")
            # Step 2: Extract Details Selectors
            details_info = await extract_details_selectors(first_job_href, browser=browser)
            final_selectors.update(details_info)
        else:
            logger.warning("Could not find a job link to navigate to! Details selectors will be empty.")
            final_selectors.update({k: "" for k in DetailsSchema.model_fields.keys()})
        
        return final_selectors
    except Exception as e:
        logger.error(f"Selectors extraction CRASHED: {e}")
        raise
    finally:
        await browser.close()
        logger.info("--- [LLM CONSOLIDATED] Finished ---")

if __name__ == "__main__":
    import asyncio
    import json
    
    async def main():
        test_url = "https://www.metacareers.com/jobsearch?page=2"
        print(f"Testing two-step autonomous AI extraction on: {test_url}")
        print("This will take 10-20 seconds as Chrome navigates and GPT analyzes the HTML...")
        
        selectors = await extract_css_selectors(test_url)
        print("\n=== 🎯 FINAL EXTRACTED SELECTORS ===")
        print(json.dumps(selectors, indent=4))
        
    asyncio.run(main())
