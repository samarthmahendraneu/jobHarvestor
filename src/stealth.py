import random
from pyppeteer.page import Page
from pyppeteer_stealth import stealth
from fake_useragent import UserAgent

# Initialize globally to maintain caching efficiency
ua = UserAgent(os=['windows', 'macos', 'linux'])

async def prepare_stealth_page(browser) -> Page:
    """
    Creates a new Pyppeteer page and aggressively strips out the 'headless' 
    Chrome fingerprint using standard OS emulation and dynamic headers.
    """
    page = await browser.newPage()
    
    # 1. Apply the core pyppeteer-stealth evasion plugins
    await stealth(page)
    
    # 2. Inject a completely random realistic User-Agent (Chrome, Edge, Firefox, Safari)
    random_ua = ua.random
    await page.setUserAgent(random_ua)
    
    # 3. Simulate realistic human-driven HTTP Headers
    await page.setExtraHTTPHeaders({
        'Accept-Language': random.choice([
            'en-US,en;q=0.9', 
            'en-GB,en;q=0.8', 
            'es-US,es;q=0.9', 
            'fr-FR,fr;q=0.9'
        ]),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0'
    })
    
    # 4. Aggressively strip Javascript Automation signatures via evaluateOnNewDocument
    await page.evaluateOnNewDocument('''
        // Nuke the webdriver attribute entirely
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        
        // Mock standard hardware concurrency and memory to avoid looking like a sterile test environment
        Object.defineProperty(navigator, 'deviceMemory', {
            get: () => 8
        });
        Object.defineProperty(navigator, 'hardwareConcurrency', {
            get: () => 4
        });
        
        // Mock generic browser plugins
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3] 
        });
    ''')
    
    # 5. Mask the window resolution to look like a standard monitor
    width = random.choice([1920, 1440, 1366, 1536])
    height = random.choice([1080, 900, 768, 864])
    await page.setViewport({'width': width, 'height': height})

    return page