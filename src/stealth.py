import random
from pyppeteer_stealth import stealth

# 🔹 Stable curated UA pool (better than fake-useragent)
USER_AGENTS = [
    # Chrome Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",

    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.140 Safari/537.36",

    # Chrome Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",

    # Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
]

ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.8",
]


async def prepare_stealth_page(browser):
    page = await browser.newPage()

    # 1. User-Agent rotation
    ua = random.choice(USER_AGENTS)
    await page.setUserAgent(ua)

    # 2. Headers (important for fingerprint consistency)
    await page.setExtraHTTPHeaders({
        "Accept-Language": random.choice(ACCEPT_LANGUAGES),
        "Upgrade-Insecure-Requests": "1",
    })

    # 3. Viewport randomization
    await page.setViewport(random.choice(VIEWPORTS))

    # 4. Timezone spoof (basic)
    try:
        await page.emulateTimezone("America/New_York")
    except Exception:
        pass

    # 5. Stealth plugin (CRITICAL)
    await stealth(page)

    # 6. Minor behavior randomization (optional but good)
    await page.evaluateOnNewDocument("""
        () => {
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
        }
    """)

    return page