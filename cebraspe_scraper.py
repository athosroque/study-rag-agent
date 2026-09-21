from playwright.sync_api import sync_playwright
import time
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("https://www.cebraspe.org.br/concursos/")
    page.wait_for_selector(".loader", state="hidden", timeout=15000)
    time.sleep(2)
    print(page.content()[:1000])
    browser.close()
