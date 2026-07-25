from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import time

def debug_page():
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        locale='es-ES',
        timezone_id='America/Bogota'
    )
    page = context.new_page()
    page.set_default_timeout(30000)
    
    url = "https://co.mileroticos.com/escorts/bogota/"
    print(f"Navegando a {url}...")
    
    page.goto(url, wait_until='domcontentloaded', timeout=30000)
    
    print("Esperando a que Cloudflare se resuelva...")
    # Esperar más tiempo para que Cloudflare y JavaScript resuelvan el desafío
    for i in range(15):
        time.sleep(1)
        html_content = page.content()
        # Verificar si la página de Cloudflare sigue ahí
        if "challenge" not in html_content.lower() or len(html_content) > 100000:
            print(f"  Contenido cargado después de {i+1} segundos")
            break
        print(f"  Esperando... ({i+1}/15)")
    
    # Obtener el HTML final
    html_content = page.content()
    
    # Guardar en archivo para inspeccionarlo
    with open('debug_output.html', 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"HTML guardado en debug_output.html ({len(html_content)} bytes)")
    
    # También analizar con BeautifulSoup
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Buscar elementos comunes
    print("\nBuscando elementos...")
    print(f"Divs con id 'no-grid': {len(soup.find_all('div', id='no-grid'))}")
    print(f"Divs con class 'item-box': {len(soup.find_all('div', class_=lambda x: x and 'item-box' in x))}")
    print(f"Artículos con class 'anun': {len(soup.find_all('article', class_='anun'))}")
    
    # Ver título
    title = soup.find('title')
    if title:
        print(f"Título: {title.string}")
    
    # Ver si aún tiene contenido de Cloudflare
    if "challenge" in html_content.lower() or "un momento" in html_content.lower():
        print("\n⚠️  La página aún está mostrando el desafío de Cloudflare")
        print("Los primeros 500 caracteres:")
        print(html_content[:500])
    else:
        print("\n✓ La página parece haber cargado correctamente")
    
    browser.close()
    playwright.stop()
    
    print("\nDepuración completada. Revisa debug_output.html")

if __name__ == "__main__":
    debug_page()
