from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import time
from urllib.parse import urljoin
import re

class MileroticosScraper:
    def __init__(self, base_url="https://co.mileroticos.com"):
        """
        Inicializa el scraper con URL base configurable
        Usa Playwright con Chromium para ejecutar JavaScript y pasar Cloudflare
        """
        self.base_url = base_url
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=True,  # No mostrar ventana del navegador
            args=['--no-sandbox', '--disable-setuid-sandbox']
        )
        self.context = self.browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            locale='es-ES',
            timezone_id='America/Bogota'
        )
        self.page = self.context.new_page()
        # Timeout más corto para evitar bloqueos
        self.page.set_default_timeout(30000)
    
    def close(self):
        """Cierra el navegador y libera recursos"""
        try:
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
        except Exception as e:
            print(f"Advertencia al cerrar el navegador: {e}")
    
    def get_listing_urls(self, urls, max_pages=1):
        """
        Obtiene las URLs de los anuncios desde las páginas de listado
        """
        all_ad_urls = []
        
        for url in urls:
            # Limpiar URL y preparar para paginación
            base_path = url.rstrip('/')
            
            for page in range(1, max_pages + 1):
                if page == 1:
                    full_url = urljoin(self.base_url, base_path + '/')
                else:
                    full_url = urljoin(self.base_url, f"{base_path}/pagina-{page}/")
                
                print(f"Procesando: {full_url}")
                
                try:
                    # Navegar a la URL
                    self.page.goto(full_url, wait_until='networkidle')
                    
                    # Esperar a que los elementos se carguen (intentar esperar por divs de anuncios)
                    try:
                        self.page.wait_for_selector('[class*="item-box"], [class*="anun"], div[class*="grid"]', timeout=20000)
                    except:
                        pass  # Continuar aunque no encuentre el selector
                    
                    time.sleep(3)
                    
                    # Obtener el HTML
                    html_content = self.page.content()
                    
                    # Depuración: imprimir parte del HTML
                    if len(html_content) < 1000:
                        print(f"  Advertencia: HTML muy pequeño ({len(html_content)} bytes)")
                        print(f"  Primeros 500 chars: {html_content[:500]}")
                    
                    soup = BeautifulSoup(html_content, 'html.parser')
                    
                    # Buscar anuncios - probar múltiples selectores
                    ad_containers = None
                    
                    # Intentar diferentes selectores
                    no_grid = soup.find('div', id='no-grid')
                    if no_grid:
                        ad_containers = no_grid.find_all('div', recursive=False)
                    else:
                        # Buscar otros contenedores posibles
                        item_boxes = soup.find_all('div', class_=lambda x: x and 'item-box' in x)
                        if item_boxes:
                            ad_containers = item_boxes
                        else:
                            # Buscar cualquier div con enlace que pueda ser un anuncio
                            potential_ads = soup.find_all('div', class_=lambda x: x and 'anun' in x.lower())
                            if potential_ads:
                                ad_containers = potential_ads
                    
                    if ad_containers:
                        print(f"  Encontrados {len(ad_containers)} anuncios")
                        
                        for container in ad_containers:
                            # Buscar el primer enlace 'a' dentro del contenedor
                            link = container.find('a', href=True)
                            if link and link.get('href'):
                                href = link['href']
                                # Si es relativo, construir URL completa
                                if not href.startswith('http'):
                                    ad_url = urljoin(self.base_url, href)
                                else:
                                    ad_url = href
                                all_ad_urls.append(ad_url)
                    else:
                        print(f"  No se encontraron anuncios en {full_url}")
                    
                    # Pausa entre páginas
                    time.sleep(8)
                    
                except Exception as e:
                    print(f"Error al procesar {full_url}: {type(e).__name__}: {e}")
                    # Esperar más tiempo si hay error
                    time.sleep(15)
                    continue
        
        # Eliminar duplicados manteniendo el orden
        seen = set()
        unique_urls = []
        for url in all_ad_urls:
            if url not in seen:
                seen.add(url)
                unique_urls.append(url)
        
        return unique_urls
    
    def scrape_ad_details(self, ad_url, retries=2):
        """
        Extrae los datos básicos de un anuncio individual con reintentos
        """
        for attempt in range(retries):
            try:
                # Navegar a la URL del anuncio
                self.page.goto(ad_url, wait_until='load')
                
                # Esperar a que el contenido se cargue completamente
                time.sleep(2)
                
                # Obtener el HTML
                html_content = self.page.content()
                soup = BeautifulSoup(html_content, 'html.parser')
                
                # Buscar el article con clase "anun"
                article = soup.find('article', class_='anun')
                if not article:
                    if attempt < retries - 1:
                        time.sleep(10)  # Esperar antes de reintentar
                        continue
                    return None
                
                data = self._extract_ad_data(article, ad_url)
                return data
                
            except Exception as e:
                print(f"Error al procesar {ad_url} (intento {attempt + 1}/{retries}): {e}")
                if attempt < retries - 1:
                    time.sleep(10)  # Esperar antes de reintentar
                else:
                    return None
        
        return None
    
    def _extract_ad_data(self, article, ad_url):
        """
        Extrae los datos del artículo
        """
        data = {
            'url': ad_url,
            'titulo': None,
            'id': None,
            'fecha': None,
            'ubicacion': None,
            'telefono': None,
            'whatsapp': None,
            'telegram': None,
            'web': None,
            'descripcion': None,
            'imagen_principal': None,
            'imagenes': [],
            'verificado_identidad': False,
            'fotos_verificadas': False,
            'no_anticipo': False,
            'confiabilidad': None,
            'visitas_hoy': None,
            'visitas_total': None,
            'listado_hoy': None,
            'listado_total': None
        }
        
        # Extraer título
        h1 = article.find('h1', class_='title_viewad')
        if h1:
            link = h1.find('a')
            if link:
                data['titulo'] = link.get_text(strip=True)
        
        # Extraer ID y fecha de la cabecera
        header_section = article.find('section', class_='anun-header')
        if header_section:
            # ID
            id_span = header_section.find('span', class_='itemm-label')
            if id_span:
                id_text = id_span.get_text(strip=True)
                if 'ID:' in id_text:
                    data['id'] = id_text.replace('ID:', '').strip()
            
            # Fecha - buscar en varios lugares
            date_elements = header_section.find_all('span', string=lambda x: x and 'Jul' in str(x))
            for date_span in date_elements:
                date_text = date_span.get_text(strip=True)
                if any(month in date_text for month in ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']):
                    data['fecha'] = date_text
                    break
            
            # Ubicación
            title_right = header_section.find('div', class_='title-right')
            if title_right:
                ubicacion_text = title_right.get_text(strip=True)
                if 'Ubicación:' in ubicacion_text:
                    data['ubicacion'] = ubicacion_text.replace('Ubicación:', '').strip()
        
        # Extraer teléfono, WhatsApp y Telegram
        # Teléfono
        phone_span = article.find('span', class_='fog-tel-stats')
        if phone_span and phone_span.get('data-l'):
            phone_data = phone_span.get('data-l')
            if 'tel:' in phone_data:
                data['telefono'] = phone_data.replace('tel:', '').strip()
        
        # WhatsApp
        whatsapp_span = article.find('span', class_='fog-whatsapp-stats')
        if whatsapp_span and whatsapp_span.get('data-l'):
            data['whatsapp'] = whatsapp_span.get('data-l')
        
        # Telegram
        telegram_span = article.find('span', class_='fog-telegram-stats')
        if telegram_span and telegram_span.get('data-l'):
            data['telegram'] = telegram_span.get('data-l')
        
        # Web
        web_span = article.find('span', class_='visit_web')
        if web_span and web_span.get('data-l'):
            data['web'] = web_span.get('data-l')
        
        # Extraer descripción
        desc_span = article.find('span', class_='description-ad')
        if desc_span:
            data['descripcion'] = desc_span.get_text(strip=True)
        
        # Extraer imágenes
        # Imagen principal
        img_box = article.find('div', class_='item-img-box')
        if img_box:
            img = img_box.find('img')
            if img and img.get('src'):
                data['imagen_principal'] = img['src']
        
        # Todas las imágenes del perfil
        photos_section = article.find('section', id='anun-photos-section')
        if photos_section:
            img_boxes = photos_section.find_all('div', class_='item-img-box')
            for box in img_boxes:
                img = box.find('img')
                if img and img.get('src'):
                    data['imagenes'].append(img['src'])
        
        # Extraer características de verificación
        verified_div = article.find('div', class_='verified-div')
        if verified_div:
            features = verified_div.find_all('div', class_='ad-feature')
            for feature in features:
                feature_text = feature.get_text(strip=True)
                if 'IDENTIDAD VERIFICADA' in feature_text:
                    data['verificado_identidad'] = True
                elif 'FOTOS VERIFICADAS' in feature_text:
                    data['fotos_verificadas'] = True
                elif 'NO PIDO ANTICIPO' in feature_text:
                    data['no_anticipo'] = True
        
        # Extraer confiabilidad
        reliability_container = article.find('div', class_='reliability-container')
        if reliability_container:
            progress_bar = reliability_container.find('div', class_='reliability-progress-bar')
            if progress_bar and progress_bar.get('style'):
                style = progress_bar.get('style')
                if 'width:' in style:
                    match = re.search(r'width:\s*(\d+)%', style)
                    if match:
                        data['confiabilidad'] = int(match.group(1))
        
        # Extraer estadísticas
        stats_box = article.find('div', id='stats_box')
        if stats_box:
            visitas_hoy = stats_box.find('span', id='detail_today')
            if visitas_hoy:
                data['visitas_hoy'] = visitas_hoy.get_text(strip=True)
            
            visitas_total = stats_box.find('span', id='detail_total')
            if visitas_total:
                data['visitas_total'] = visitas_total.get_text(strip=True)
            
            listado_hoy = stats_box.find('span', id='list_today')
            if listado_hoy:
                data['listado_hoy'] = listado_hoy.get_text(strip=True)
            
            listado_total = stats_box.find('span', id='list_total')
            if listado_total:
                data['listado_total'] = listado_total.get_text(strip=True)
        
        return data
    
    def scrape_all(self, urls, max_ads=None, max_pages=1, delay=5):
        """
        Procesa todas las URLs de categorías y extrae los datos de los anuncios
        """
        try:
            print("Obteniendo URLs de anuncios...")
            ad_urls = self.get_listing_urls(urls, max_pages=max_pages)
            
            if max_ads:
                ad_urls = ad_urls[:max_ads]
            
            print(f"\nSe encontraron {len(ad_urls)} anuncios. Procesando...")
            
            results = []
            for i, ad_url in enumerate(ad_urls, 1):
                print(f"Procesando anuncio {i}/{len(ad_urls)}: {ad_url}")
                data = self.scrape_ad_details(ad_url)
                if data:
                    results.append(data)
                    print(f"  ✓ Extraído: {data.get('titulo', 'Sin título')[:50]}...")
                else:
                    print(f"  ✗ Falló al extraer datos")
                
                # Pausa entre peticiones
                if i < len(ad_urls):
                    time.sleep(delay)
            
            return results
        finally:
            # Siempre cerrar el navegador al terminar
            self.close()


# Ejemplo de uso
if __name__ == "__main__":
    # Configurar la URL base
    BASE_URL = "https://co.mileroticos.com"
    
    # Lista de URLs (puedes usar todas o una muestra)
    urls = [
        "/escorts/amazonas/",
        "/escorts/antioquia/",
        "/escorts/arauca/",
        "/escorts/atlantico/",
        "/escorts/bogota/",
        "/escorts/bolivar/",
        "/escorts/boyaca/",
        "/escorts/caldas/",
        "/escorts/caqueta/",
        "/escorts/casanare/",
        "/escorts/cauca/",
        "/escorts/cesar/",
        "/escorts/choco/",
        "/escorts/cordoba/",
        "/escorts/cundinamarca/",
        "/escorts/guainia/",
        "/escorts/guaviare/",
        "/escorts/huila/",
        "/escorts/la-guajira/",
        "/escorts/magdalena/",
        "/escorts/meta/",
        "/escorts/narino/",
        "/escorts/norte-de-santander/",
        "/escorts/putumayo/",
        "/escorts/quindio/",
        "/escorts/risaralda/",
        "/escorts/san-andres-y-providencia/",
        "/escorts/santander/",
        "/escorts/sucre/",
        "/escorts/tolima/",
        "/escorts/valle-del-cauca/",
        "/escorts/vaupes/",
        "/escorts/vichada/",
    ]
    
    # Crear scraper
    scraper = MileroticosScraper(base_url=BASE_URL)
    
    # Procesar anuncios
    # max_pages: número de páginas por categoría
    # max_ads: número máximo de anuncios totales
    # delay: tiempo entre peticiones de anuncios individuales
    resultados = scraper.scrape_all(
        urls, 
        max_ads=20,  # Solo 20 anuncios para prueba
        max_pages=1,  # Solo primera página
        delay=15  # Mayor delay entre anuncios
    )
    
    # Mostrar resultados
    print("\n" + "="*50)
    print(f"Se extrajeron {len(resultados)} anuncios")
    print("="*50)
    
    # Guardar en formato JSON
    import json
    with open('resultados.json', 'w', encoding='utf-8') as f:
        json.dump(resultados, f, ensure_ascii=False, indent=2)
    print("\nResultados guardados en 'resultados.json'")
    
    # Mostrar resumen
    for i, anuncio in enumerate(resultados, 1):
        print(f"\n--- Anuncio {i} ---")
        print(f"ID: {anuncio.get('id', 'N/A')}")
        print(f"Título: {anuncio.get('titulo', 'N/A')}")
        print(f"Teléfono: {anuncio.get('telefono', 'N/A')}")
        print(f"Ubicación: {anuncio.get('ubicacion', 'N/A')}")
        if anuncio.get('verificado_identidad'):
            print("✓ Identidad verificada")
        if anuncio.get('fotos_verificadas'):
            print("✓ Fotos verificadas")
        if anuncio.get('no_anticipo'):
            print("✓ No pide anticipo")