import os
import re
import time
import random
import json
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse
from bs4 import BeautifulSoup
import undetected_chromedriver as uc
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from datetime import datetime, timezone


class CloudflareDetectionError(Exception):
    """Se lanza cuando el sitio sigue mostrando un desafío de Cloudflare."""


class DatabaseHandler:
    """Manejador simple para guardar anuncios en Postgres usando psycopg2.

    Lee las credenciales desde .env (usa python-dotenv).
    """
    def __init__(self):
        load_dotenv()
        self.host = os.getenv('DB_HOST', 'localhost')
        self.port = int(os.getenv('DB_PORT', '5432'))
        self.dbname = os.getenv('DB_NAME', 'postgres')
        self.user = os.getenv('DB_USER', 'postgres')
        self.password = os.getenv('DB_PASSWORD', '')
        self._conn = None
        self._connect()
        self._ensure_table()

    def _connect(self):
        self._conn = psycopg2.connect(host=self.host, port=self.port, dbname=self.dbname, user=self.user, password=self.password)
        self._conn.autocommit = True

    def _ensure_table(self):
        with self._conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS ads (
                id TEXT PRIMARY KEY,
                source_page TEXT,
                source_id TEXT,
                url TEXT UNIQUE,
                titulo TEXT,
                descripcion TEXT,
                ubicacion TEXT,
                direccion TEXT,
                imagen_principal TEXT,
                map_image TEXT,
                ad_displacements TEXT,
                contact_phone TEXT,
                contact_whatsapp TEXT,
                contact_telegram TEXT,
                features JSONB,
                images JSONB,
                video_urls JSONB,
                stats JSONB,
                scraped_at TIMESTAMP
            )
            """)
            cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_ads_source_page_source_id
            ON ads (source_page, source_id);
            """)

    def save_ad(self, data):
        source_page = data.get('source_page', 'MILER')
        source_id = data.get('source_id')
        if source_id:
            id_val = f"{source_page}:{source_id}"
        else:
            id_val = data.get('url')
        now = datetime.now(timezone.utc)
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ads (id,source_page,source_id,url,titulo,descripcion,ubicacion,direccion,imagen_principal,map_image,ad_displacements,contact_phone,contact_whatsapp,contact_telegram,features,images,video_urls,stats,scraped_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (source_page, source_id) DO UPDATE SET
                    url=EXCLUDED.url,
                    titulo=EXCLUDED.titulo,
                    descripcion=EXCLUDED.descripcion,
                    ubicacion=EXCLUDED.ubicacion,
                    direccion=EXCLUDED.direccion,
                    imagen_principal=EXCLUDED.imagen_principal,
                    map_image=EXCLUDED.map_image,
                    ad_displacements=EXCLUDED.ad_displacements,
                    contact_phone=EXCLUDED.contact_phone,
                    contact_whatsapp=EXCLUDED.contact_whatsapp,
                    contact_telegram=EXCLUDED.contact_telegram,
                    features=EXCLUDED.features,
                    images=EXCLUDED.images,
                    video_urls=EXCLUDED.video_urls,
                    stats=EXCLUDED.stats,
                    scraped_at=EXCLUDED.scraped_at
                """,
                (
                    id_val,
                    source_page,
                    source_id,
                    data.get('url'),
                    data.get('titulo'),
                    data.get('descripcion'),
                    data.get('ubicacion'),
                    data.get('direccion'),
                    data.get('imagen_principal'),
                    data.get('map_image'),
                    data.get('ad_displacements'),
                    data.get('contact_phone'),
                    data.get('contact_whatsapp'),
                    data.get('contact_telegram'),
                    psycopg2.extras.Json(data.get('features') or []),
                    psycopg2.extras.Json(data.get('images') or []),
                    psycopg2.extras.Json(data.get('video_urls') or []),
                    psycopg2.extras.Json(data.get('stats') or {}),
                    now
                )
            )


class Scraper:
    def __init__(self, base_url=None):
        """
        Inicializa el scraper usando undetected-chromedriver con perfil persistente.
        """
        self.base_url = base_url or os.getenv("BASE_URL")
        print("Iniciando navegador con undetected-chromedriver...")
        
        options = uc.ChromeOptions()
        
        # 1. PERFIL PERSISTENTE: Guarda las cookies para no repetir el captcha constantemente
        profile_dir = os.path.join(os.getcwd(), "chrome_profile")
        options.add_argument(f"--user-data-dir={profile_dir}")
        
        # Opciones de compatibilidad (Limpias de flags conflictivos con UC)
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1366,768")  # Resolución común de laptop
        
        chrome_version = os.getenv("CHROME_VERSION_MAIN", "150")
        version_main = int(chrome_version) if chrome_version.isdigit() else None

        try:
            self.driver = uc.Chrome(
                options=options,
                headless=False,  # DEBE ser False para que resuelvas el reto visualmente si aparece
                use_subprocess=True,
                version_main=version_main
            )
        except Exception as e:
            print(f"Error al iniciar con perfil: {e}")
            print("Intentando inicialización básica sin perfil persistente...")
            # Fallback en caso de bloqueo del perfil por otra instancia abierta
            try:
                self.driver = uc.Chrome(headless=False, version_main=version_main)
            except Exception as fallback_error:
                print(f"Fallback con la versión {chrome_version} falló: {fallback_error}")
                self.driver = uc.Chrome(headless=False, version_main=None)
        
        self.driver.set_page_load_timeout(45)
        print("Navegador iniciado correctamente.")
    
    def close(self):
        """Cierra el navegador de forma segura"""
        try:
            self.driver.quit()
            print("Navegador cerrado.")
        except Exception:
            pass
            
    def _is_cloudflare_challenge(self):
        """Retorna True cuando la página actual muestra un reto real de Cloudflare."""
        page_source = (self.driver.page_source or "").lower()
        title = (self.driver.title or "").lower()
        current_url = (self.driver.current_url or "").lower()

        cloudflare_markers = [
           
            "just a moment",
           
        ]

        detected_markers = [marker for marker in cloudflare_markers if marker in page_source or marker in title or marker in current_url]

        if not detected_markers:
            return False

        # Evitar falsos positivos con páginas normales que contienen la palabra "captcha"
        if "captcha" in detected_markers and "captcha" not in page_source and "captcha" not in title:
            return False

        # La presencia de la ruta de Cloudflare o del texto clásico es una señal fuerte
        return "challenge-platform" in current_url or "cf-challenge" in page_source or any(
            marker in page_source for marker in ["just a moment", "checking your browser", "enable javascript", "cloudflare"]
        )

    def bypass_cloudflare_flow(self, target_url):
        """
        Navega a la URL inicial y pausa la ejecución si detecta Cloudflare
        para permitir la interacción manual.
        """
        print(f"\nAccediendo a la página de verificación: {target_url}")
        self.driver.get(target_url)
        time.sleep(5)

        for attempt in range(6):
            if self._is_cloudflare_challenge():
                print("\n" + "!"*60)
                print("[!] CLOUDFLARE DETECTADO: El script se ha pausado.")
                print("[!] Resuelve el captcha o verificación en la ventana de Chrome.")
                print("[!] Una vez que veas la página real del sitio cargada por completo:")
                print("[!] Regresa a esta terminal y presiona ENTER para continuar...")
                print("!"*60 + "\n")

                input("Presiona ENTER aquí cuando la página haya cargado...")
                time.sleep(3)
                continue

            if self.driver.current_url and self.driver.current_url.lower() != target_url.lower():
                print(f"Acceso verificado. Se redirigió a: {self.driver.current_url}")
                break

            time.sleep(2)

        if self._is_cloudflare_challenge():
            raise CloudflareDetectionError("Cloudflare sigue mostrando el desafío después de varios intentos.")

        print("Acceso verificado. No se detecta bloqueo activo. Continuando...")

    def _build_page_url(self, base_path, page):
        parsed = urlparse(base_path)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        if page == 1:
            query.pop('p', None)
        else:
            query['p'] = str(page)

        normalized_path = parsed.path.rstrip('/') + '/'
        new_query = urlencode(query, doseq=True)
        return urljoin(self.base_url, urlunparse((parsed.scheme, parsed.netloc, normalized_path, parsed.params, new_query, parsed.fragment)))

    def _location_key_from_url(self, url):
        if not url:
            return 'resultados'

        path = url
        if url.startswith('http://') or url.startswith('https://'):
            path = urlparse(url).path

        parts = [segment for segment in path.split('/') if segment]
        if not parts:
            return 'resultados'

        return re.sub(r'[^a-zA-Z0-9_-]', '_', parts[-1]).lower()

    def _collect_listing_urls_for_page(self, base_path, page):
        page_urls = []
        full_url = self._build_page_url(base_path, page)

        print(f"Procesando listado: {full_url}")

        try:
            self.driver.get(full_url)

            # Simular un tiempo de lectura humano
            time.sleep(random.uniform(4, 7))

            html_content = self.driver.page_source
            soup = BeautifulSoup(html_content, 'html.parser')

            ad_containers = []
            selectors = [
                ('div', {'id': 'no-grid'}),
                ('div', {'class': 'list-container'}),
                ('div', {'class': 'container-list'}),
                ('div', {'class': 'results'})
            ]

            for tag, attrs in selectors:
                container = soup.find(tag, attrs)
                if container:
                    ad_containers = container.find_all('div', recursive=False)
                    if ad_containers:
                        break

            if not ad_containers:
                ad_containers = soup.find_all('div', class_=re.compile(r'(anuncio|ad|item|listing)'))

            if ad_containers:
                print(f"  Encontrados {len(ad_containers)} contenedores en esta página.")
                for container in ad_containers:
                    links = container.find_all('a', href=True)
                    for link in links:
                        href = link.get('href')
                        if self._is_ad_listing_href(href):
                            if not href.startswith('http'):
                                ad_url = urljoin(self.base_url, href)
                            else:
                                ad_url = href
                            page_urls.append(ad_url)
            else:
                print(f"  No se encontraron anuncios directamente en {full_url}")
                with open(f'debug_page_{page}.html', 'w', encoding='utf-8') as f:
                    f.write(html_content)

            time.sleep(random.uniform(5, 10))

        except Exception as e:
            print(f"Error al procesar listado {full_url}: {e}")
            time.sleep(15)

        return page_urls

    def get_listing_urls(self, urls, max_pages=1):
        """
        Obtiene las URLs de los anuncios desde las páginas de listado
        """
        all_ad_urls = []

        for url in urls:
            base_path = url.rstrip('/')

            for page in range(1, max_pages + 1):
                page_urls = self._collect_listing_urls_for_page(base_path, page)
                all_ad_urls.extend(page_urls)

        # Remover duplicados manteniendo el orden original
        seen = set()
        unique_urls = []
        for u in all_ad_urls:
            if u not in seen:
                seen.add(u)
                unique_urls.append(u)

        print(f"Total de URLs únicas encontradas: {len(unique_urls)}")
        return unique_urls

    def _is_ad_listing_href(self, href):
        if not href:
            return False

        href = href.strip()
        normalized = href.split('?', 1)[0].split('#', 1)[0]

        if re.search(r'/anuncio/\d+', normalized):
            return True

        if re.search(r'/escorts/.+/\d+/?$', normalized, re.IGNORECASE):
            return True

        return False

    def _normalize_src_url(self, src):
        if not src:
            return None

        src = src.strip()
        if not src or ' ' in src:
            return None

        if src.startswith('//'):
            return f'https:{src}'
        if src.startswith('http://') or src.startswith('https://'):
            return src
        if src.startswith('/'):
            return urljoin(self.base_url, src)
        if src.startswith('./'):
            return urljoin(self.base_url, src[2:])

        return src

    def _extract_image_urls(self, parent):
        urls = []
        seen = set()

        for img in parent.find_all('img'):
            candidates = []
            if img.has_attr('data-srcset'):
                candidates.extend([item.strip().split()[0] for item in img['data-srcset'].split(',') if item.strip()])
            elif img.has_attr('srcset'):
                candidates.extend([item.strip().split()[0] for item in img['srcset'].split(',') if item.strip()])
            elif img.has_attr('data-src'):
                candidates.append(img['data-src'].strip())
            elif img.has_attr('src'):
                candidates.append(img['src'].strip())

            for candidate in candidates:
                if 'banner' in candidate.lower():
                    continue
                url = self._normalize_src_url(candidate)
                if url and url not in seen:
                    seen.add(url)
                    urls.append(url)

        return urls

    def _extract_video_urls(self, parent):
        urls = []
        seen = set()

        for iframe in parent.find_all('iframe'):
            src = iframe.get('data-src') or iframe.get('src')
            url = self._normalize_src_url(src)
            if url and url not in seen:
                seen.add(url)
                urls.append(url)

        return urls

    def _extract_map_image(self, parent):
        map_section = parent.find('section', id='anun-map-section')
        if not map_section:
            map_section = parent.find('div', class_=re.compile(r'(map|location-map)', re.IGNORECASE))

        if not map_section:
            return None

        img = map_section.find('img')
        if not img:
            return None

        return self._normalize_src_url(img.get('src') or img.get('data-src'))

    def _extract_contacts(self, parent):
        contacts = {
            'phone': None,
            'whatsapp': None,
            'telegram': None
        }

        for tag in parent.find_all(attrs={'data-l': True}):
            value = re.sub(r'\s+', '', tag['data-l']).strip()
            if value.lower().startswith('tel:'):
                contacts['phone'] = value.split(':', 1)[1]
            elif 'api.whatsapp.com' in value or 'whatsapp' in value.lower():
                contacts['whatsapp'] = value
            elif 't.me' in value or 'telegram' in value.lower():
                contacts['telegram'] = value

        if not contacts['phone'] and contacts['whatsapp']:
            try:
                parsed = urlparse(contacts['whatsapp'])
                query = dict(parse_qsl(parsed.query, keep_blank_values=True))
                phone = None
                if 'phone' in query:
                    phone = query['phone']
                else:
                    whatsapp_path = parsed.path.strip('/')
                    if whatsapp_path:
                        candidate = re.sub(r'[^0-9+]', '', whatsapp_path)
                        if re.search(r'\d{7,15}', candidate):
                            phone = candidate
                if not phone:
                    phone_search = re.search(r'(\+?\d{7,15})', contacts['whatsapp'])
                    if phone_search:
                        phone = phone_search.group(1)
                if phone:
                    contacts['phone'] = phone
            except Exception:
                pass

        return contacts

    def _extract_text_from_description(self, desc_tag):
        if not desc_tag:
            return None

        paragraphs = [p.get_text(strip=True) for p in desc_tag.find_all(['p', 'span']) if p.get_text(strip=True)]
        if paragraphs:
            return '\n'.join(paragraphs)

        return desc_tag.get_text(separator=' ', strip=True)

    def _extract_location_from_description(self, desc_text):
        if not desc_text:
            return None

        for line in desc_text.splitlines():
            if line.lower().startswith('ubicación:'):
                return line.split(':', 1)[1].strip()
        return None

    def _extract_address_from_description(self, desc_text):
        if not desc_text:
            return None

        lines = [line.strip() for line in desc_text.splitlines() if line.strip()]
        for line in lines:
            if re.search(r'\b(calle|cra|carrera|av\.?|avenida|#)\b', line, re.IGNORECASE):
                return line
        return None

    def _extract_ad_data(self, article, ad_url):
        """
        Estructura de extracción interna de campos
        """
        data = {
            'url': ad_url,
            'titulo': None,
            'source_id': None,
            'source_page': 'MILER',
            'ubicacion': None,
            'direccion': None,
            'descripcion': None,
            'ad_displacements': None,
            'features': [],
            'images': [],
            'video_urls': [],
            'map_image': None,
            'contact_phone': None,
            'contact_whatsapp': None,
            'contact_telegram': None,
            'stats': {},
            'imagen_principal': None
        }
        
        h1 = article.find('h1', class_='title_viewad')
        if h1:
            link = h1.find('a')
            data['titulo'] = link.get_text(strip=True) if link else h1.get_text(strip=True)

        if ad_url:
            id_match = re.search(r'/anuncio/(\d+)', ad_url)
            if id_match:
                data['source_id'] = id_match.group(1)

        id_tag = article.find('span', class_='itemm-label')
        if id_tag and not data['source_id']:
            id_match = re.search(r'\d+', id_tag.get_text())
            if id_match:
                data['source_id'] = id_match.group(0)

        desc_tag = article.find('span', class_='description-ad') or article.find('div', class_='description') or article.find('div', class_='content')
        data['descripcion'] = self._extract_text_from_description(desc_tag)

        data['ubicacion'] = self._extract_location_from_description(data['descripcion'])
        data['direccion'] = self._extract_address_from_description(data['descripcion'])

        ad_disp = article.find('div', class_='ad-displacements')
        if ad_disp:
            data['ad_displacements'] = ad_disp.get_text(strip=True)

        features = [feature.get_text(strip=True) for feature in article.find_all('div', class_='ad-feature') if feature.get_text(strip=True)]
        data['features'] = features

        images_section = article.find('section', id='anun-photos-section') or article
        images = self._extract_image_urls(images_section)
        if images:
            data['images'] = images
            data['imagen_principal'] = images[0]
        else:
            main_image = article.find('img', class_='img-fluid') or article.find('img', class_='front-img')
            if main_image and main_image.get('src'):
                data['imagen_principal'] = self._normalize_src_url(main_image['src'])
                if data['imagen_principal']:
                    data['images'] = [data['imagen_principal']]

        data['map_image'] = self._extract_map_image(article)
        if data['map_image'] and data['map_image'] not in data['images']:
            data['images'].append(data['map_image'])
        if data['map_image'] and not data['imagen_principal']:
            data['imagen_principal'] = data['map_image']

        data['video_urls'] = self._extract_video_urls(article)

        contacts = self._extract_contacts(article)
        data['contact_phone'] = contacts['phone']
        data['contact_whatsapp'] = contacts['whatsapp']
        data['contact_telegram'] = contacts['telegram']

        location_tag = article.find('div', class_=re.compile(r'(title-right|location|locale)', re.IGNORECASE))
        if location_tag and not data['ubicacion']:
            data['ubicacion'] = location_tag.get_text(strip=True)

        stats_labels = [label.get_text(strip=True) for label in article.find_all('span', class_='label-stat')]
        stats_values = [value.get_text(strip=True) for value in article.find_all('span', class_='value-stat')]
        if stats_labels and stats_values and len(stats_labels) == len(stats_values):
            data['stats'] = {stats_labels[i]: stats_values[i] for i in range(len(stats_labels))}

        return data

    def scrape_ad_details(self, ad_url, retries=2):
        """
        Extrae datos de un anuncio individual
        """
        for attempt in range(retries):
            try:
                self.driver.get(ad_url)
                time.sleep(random.uniform(3, 6))
                
                html_content = self.driver.page_source
                soup = BeautifulSoup(html_content, 'html.parser')
                
                article = soup.find('article', class_='anun')
                if not article:
                    article = soup.find('div', class_=re.compile(r'(anuncio|ad-detail|detail)'))
                
                if not article:
                    if attempt < retries - 1:
                        print("  No se encontró la estructura de datos, reintentando...")
                        time.sleep(10)
                        continue
                    return None
                
                data = self._extract_ad_data(article, ad_url)
                return data
                
            except Exception as e:
                print(f"Error en {ad_url} (intento {attempt + 1}/{retries}): {e}")
                if attempt < retries - 1:
                    time.sleep(10)
        
        return None
    
    def scrape_all(self, urls, max_ads=None, max_pages=1, delay=5, save_per_page=False, start_page=1, db=None):
        """
        Controla el flujo completo: validación de seguridad inicial y extracción.

        start_page: página de listado desde la cual comienza o reanuda la extracción.
        """
        try:
            # 2. ENTRADA DE SEGURIDAD: Navegamos primero para verificar / resolver Cloudflare
            url_inicial = urljoin(self.base_url, urls[0])
            self.bypass_cloudflare_flow(url_inicial)
            
            results = []
            if start_page > 1:
                print(f"Reanudando desde la página {start_page}...")
            
            for url in urls:
                base_path = url.rstrip('/')
                location_key = self._location_key_from_url(base_path)
                location_filename = f'{location_key}_resultados.json'
                location_results = []

                for page in range(start_page, max_pages + 1):
                    page_urls = self._collect_listing_urls_for_page(base_path, page)

                    if max_ads:
                        remaining = max_ads - len(results)
                        if remaining <= 0:
                            break
                        page_urls = page_urls[:remaining]

                    for i, ad_url in enumerate(page_urls, 1):
                        print(f"Procesando página {page}, anuncio {i}/{len(page_urls)}: {ad_url}")
                        data = self.scrape_ad_details(ad_url)
                        if data:
                            results.append(data)
                            location_results.append(data)
                            print(f"  ✓ Extraído: {data.get('titulo', 'Sin título')}")
                            if db:
                                try:
                                    db.save_ad(data)
                                    print("  → Guardado en DB (Postgres)")
                                except Exception as e:
                                    print(f"  ! Error guardando en DB: {e}")
                        else:
                            print("  ✗ No se pudo extraer.")

                        if max_ads and len(results) >= max_ads:
                            break

                        if i < len(page_urls):
                            delay_time = random.uniform(delay, delay * 1.5)
                            print(f"  Esperando {delay_time:.1f} segundos...")
                            time.sleep(delay_time)

                    if save_per_page:
                        with open(location_filename, 'w', encoding='utf-8') as f:
                            json.dump(location_results, f, ensure_ascii=False, indent=2)
                        print(f"Resultados parciales guardados en '{location_filename}'")

                    if max_ads and len(results) >= max_ads:
                        print(f"Se alcanzó el límite max_ads={max_ads}. Terminando extracción.")
                        break

                if max_ads and len(results) >= max_ads:
                    break
            
            return results
        finally:
            self.close()

# Ejemplo de uso
if __name__ == "__main__":

    urls_de_busqueda = [
        "/escorts/bogota/",
        "/escorts/amazonas/",
        "/escorts/antioquia/",
        "/escorts/arauca/",
        "/escorts/atlantico/",
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
        "/escorts/vaupes/",
        "/escorts/vichada/",
        "/escorts/valle-del-cauca/",
    ]
    
    scraper = Scraper()
    db = None
    try:
        try:
            db = DatabaseHandler()
            print("Conectado a Postgres.")
        except Exception as e:
            print(f"No fue posible conectar a la DB: {e}. Continuando sin persistencia DB.")

        resultados = scraper.scrape_all(
            urls_de_busqueda,
            max_ads=5000,
            max_pages=50,
            delay=6,             # Intervalo preventivo recomendado
            save_per_page=True,  # Guarda resultados parciales por página
            start_page=1,        # Reanuda desde la página deseada
            db=db
        )
        
        print("\n" + "="*50)
        print(f"Extracción finalizada. {len(resultados)} anuncios guardados.")
        print("="*50)
        
        with open('resultados.json', 'w', encoding='utf-8') as f:
            json.dump(resultados, f, ensure_ascii=False, indent=2)
            
        print("Resultados almacenados con éxito en 'resultados.json'")
        
    except Exception as e:
        print(f"Ocurrió un error inesperado: {e}")
        scraper.close()