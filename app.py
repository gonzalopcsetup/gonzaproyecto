from flask import Flask, render_template, jsonify, send_from_directory
import requests
import ssl
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context
import feedparser
import re
import json
import os
import time
from datetime import datetime, timedelta

# --- 1. CONFIGURACIÓN SSL (Para servidores antiguos) ---
class LegacySSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context(ciphers='DEFAULT@SECLEVEL=0')
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        kwargs['ssl_context'] = context
        return super(LegacySSLAdapter, self).init_poolmanager(*args, **kwargs)

app = Flask(__name__, static_folder='static')

# --- 2. FUNCIÓN AUXILIAR PARA RSS CON TIMEOUT ---
def parse_rss_with_timeout(url, timeout=20):
    """
    Parse RSS with timeout using requests.
    Timeout is set to 20 seconds for Render free tier.
    """
    try:
        response = requests.get(url, timeout=timeout, verify=False)
        response.raise_for_status()
        return feedparser.parse(response.content)
    except requests.Timeout:
        raise Exception(f"Timeout al acceder a {url}")
    except requests.RequestException as e:
        raise Exception(f"Error de red al acceder a {url}: {str(e)}")
    except Exception as e:
        raise Exception(f"Error al procesar RSS: {str(e)}")

# --- 3. ARCHIVOS PARA GUARDAR DATOS ---
DATA_FILE = "alturas_historico.json"
PILOTE_HISTORY_FILE = "pilote_historico.json"
SUDESTADA_FILE = "sudestada_actual.json"

def guardar_altura_sf(altura, hora):
    """Guarda la altura de San Fernando en archivo JSON"""
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                datos = json.load(f)
        else:
            datos = {"sf": []}
        
        nuevo_registro = {
            "altura": float(altura),
            "hora": hora,
            "timestamp": datetime.now().isoformat()
        }
        
        datos["sf"].append(nuevo_registro)
        
        if len(datos["sf"]) > 72:
            datos["sf"] = datos["sf"][-72:]
        
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
            
    except Exception as e:
        print(f"Error guardando altura SF: {e}")

def calcular_tendencia_sf():
    """Calcula si el río sube o baja en San Fernando - VERSIÓN SIMPLE"""
    try:
        if not os.path.exists(DATA_FILE):
            return {"tendencia": "estable", "cambio": 0, "icono": "↔️"}
        
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            datos = json.load(f)
        
        registros = datos.get("sf", [])
        
        if len(registros) < 2:
            return {"tendencia": "estable", "cambio": 0, "icono": "↔️"}
        
        # Tomar el registro más reciente
        altura_actual = registros[-1]["altura"]
        
        # Buscar hacia atrás hasta encontrar un registro diferente
        altura_anterior = None
        
        # Empezar desde el penúltimo registro y buscar hacia atrás
        for i in range(len(registros) - 2, -1, -1):
            if abs(registros[i]["altura"] - altura_actual) > 0.001:  # Si hay diferencia (aunque sea pequeña)
                altura_anterior = registros[i]["altura"]
                break
        
        # Si no encontramos ningún registro diferente, usar el más antiguo disponible
        if altura_anterior is None and len(registros) > 1:
            altura_anterior = registros[0]["altura"]
        
        # Si aún no hay anterior, usar el penúltimo
        if altura_anterior is None and len(registros) >= 2:
            altura_anterior = registros[-2]["altura"]
        
        # Calcular cambio
        if altura_anterior is not None:
            cambio = round(altura_actual - altura_anterior, 2)
            
            # Determinar tendencia (umbral simple)
            if cambio > 0.02:  # Subió más de 2cm
                return {"tendencia": "subiendo", "cambio": cambio, "icono": "⬆️"}
            elif cambio < -0.02:  # Bajó más de 2cm
                return {"tendencia": "bajando", "cambio": cambio, "icono": "⬇️"}
        
        return {"tendencia": "estable", "cambio": 0, "icono": "↔️"}
        
    except Exception as e:
        print(f"Error calculando tendencia: {e}")
        return {"tendencia": "error", "cambio": 0, "icono": "❓"}

# --- 3. FUNCIONES PARA PILOTE NORDEN (SUDESTADA) ---
def guardar_altura_pilote_en_historico(altura, hora):
    """Guarda la altura del Pilote Norden en histórico (últimos 100 registros)"""
    try:
        if os.path.exists(PILOTE_HISTORY_FILE):
            with open(PILOTE_HISTORY_FILE, 'r', encoding='utf-8') as f:
                datos = json.load(f)
        else:
            datos = {"registros": []}
        
        nuevo_registro = {
            "altura": float(altura),
            "hora": hora,
            "timestamp": datetime.now().isoformat(),
            "timestamp_unix": time.time()
        }
        
        datos["registros"].append(nuevo_registro)
        
        if len(datos["registros"]) > 100:
            datos["registros"] = datos["registros"][-100:]
        
        with open(PILOTE_HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
            
        # Verificar si hay sudestada (≥2m) y actualizar pico
        if float(altura) >= 2.0:
            actualizar_pico_sudestada(float(altura), hora)
            
    except Exception as e:
        print(f"Error guardando histórico Pilote: {e}")

def actualizar_pico_sudestada(altura_actual, hora_actual):
    """Actualiza el pico máximo durante la sudestada actual"""
    try:
        if os.path.exists(SUDESTADA_FILE):
            with open(SUDESTADA_FILE, 'r', encoding='utf-8') as f:
                sudestada = json.load(f)
        else:
            sudestada = {
                "activa": False,
                "pico_maximo": 0,
                "hora_pico": None,
                "timestamp_pico": None,
                "inicio": None
            }
        
        # Si es la primera vez que supera 2m, iniciar sudestada
        if not sudestada["activa"] and altura_actual >= 2.0:
            sudestada["activa"] = True
            sudestada["pico_maximo"] = altura_actual
            sudestada["hora_pico"] = hora_actual
            sudestada["timestamp_pico"] = time.time()
            sudestada["inicio"] = datetime.now().isoformat()
            print(f"⚠️ SUDESTADA DETECTADA: {altura_actual}m a las {hora_actual}")
        
        # Si ya está activa y encontramos un nuevo pico
        elif sudestada["activa"] and altura_actual > sudestada["pico_maximo"]:
            sudestada["pico_maximo"] = altura_actual
            sudestada["hora_pico"] = hora_actual
            sudestada["timestamp_pico"] = time.time()
            print(f"⚠️ NUEVO PICO SUDESTADA: {altura_actual}m (anterior: {sudestada['pico_maximo']}m)")
        
        # Si bajó de 1.8m por más de 4 horas, considerar sudestada terminada
        elif sudestada["activa"] and altura_actual < 1.8:
            if sudestada["timestamp_pico"]:
                tiempo_transcurrido = time.time() - sudestada["timestamp_pico"]
                if tiempo_transcurrido > 14400:  # 4 horas
                    sudestada["activa"] = False
                    print(f"✅ SUDESTADA TERMINADA. Pico máximo: {sudestada['pico_maximo']}m")
        
        with open(SUDESTADA_FILE, 'w', encoding='utf-8') as f:
            json.dump(sudestada, f, ensure_ascii=False, indent=2)
            
    except Exception as e:
        print(f"Error actualizando pico sudestada: {e}")

# --- 4. CONFIGURACIÓN DE URL Y CABECERAS ---
BASE_URL = "https://meteo.comisionriodelaplata.org/"
TARGET_URL = "https://meteo.comisionriodelaplata.org/ecsCommand.php?c=telemetry%2FupdateTelemetry&s=0.21097539498237183"

PAYLOAD = {
    'p': '1',
    'p1': '2',
    'p2': '1',
    'p3': '1'
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': '*/*',
    'Accept-Language': 'es-ES,es;q=0.9',
    'Referer': 'https://meteo.comisionriodelaplata.org/',
    'Origin': 'https://meteo.comisionriodelaplata.org',
    'X-Requested-With': 'XMLHttpRequest',
    'Connection': 'keep-alive'
}

# --- 5. RUTAS PRINCIPALES ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory('static', 'manifest.json')

@app.route('/favicon.ico')
def favicon():
    return send_from_directory('static', 'favicon.ico')

@app.route('/apple-touch-icon.png')
def apple_touch_icon():
    return send_from_directory('static', 'apple-touch-icon.png')

# --- 6. RSS Y ALERTAS ---
RSS_URL = "https://www.hidro.gob.ar/RSS/AACrioplarss.asp"

@app.route("/api/alertas")
def get_alertas():
    try:
        feed = parse_rss_with_timeout(RSS_URL, timeout=20)
        descriptions = [entry.description for entry in feed.entries]
        return jsonify(descriptions)
    except Exception as e:
        print(f"Error fetching alertas: {e}")
        return jsonify({"error": "Servicio temporalmente no disponible"}), 503

# --- 7. ALTURA SAN FERNANDO ---
@app.route("/api/altura_sf")
def api_altura_sf():
    try:
        feed = parse_rss_with_timeout("https://www.hidro.gob.ar/rss/AHrss.asp", timeout=20)

        for entry in feed.entries:
            desc = entry.description
            text = re.sub(r"<[^>]+>", "", desc)

            match = re.search(
                r"San Fernando:\s*([\d,]+)\s*m",
                text,
                re.IGNORECASE
            )

            if match:
                altura = match.group(1).replace(",", ".")
                hora_match = re.search(
                    r"FECHA y HORA:\s*([0-9/:\s]+)",
                    text
                )
                
                hora = hora_match.group(1).strip() if hora_match else None
                
                guardar_altura_sf(altura, hora)
                
                tendencia = calcular_tendencia_sf()
                
                return {
                    "altura": altura,
                    "hora": hora,
                    "tendencia": tendencia["tendencia"],
                    "cambio": tendencia["cambio"],
                    "icono": tendencia["icono"]
                }

        return {"error": "No se encontraron datos de San Fernando"}, 404

    except Exception as e:
        print(f"Error fetching San Fernando: {e}")
        return {"error": "Servicio temporalmente no disponible"}, 503

@app.route("/api/tendencia_sf")
def get_tendencia_sf():
    try:
        tendencia = calcular_tendencia_sf()
        return jsonify(tendencia)
    except Exception as e:
        return jsonify({"error": str(e)})

# --- 8. TELEMETRÍA PILOTE NORDEN ---
@app.route('/api/telemetria', methods=['GET'])
def get_telemetry():
    try:
        session = requests.Session()
        session.mount('https://', LegacySSLAdapter())
        session.headers.update(HEADERS)
        
        session.get(BASE_URL, verify=False, timeout=10)
        response = session.post(TARGET_URL, data=PAYLOAD, verify=False, timeout=10)
        
        if response.status_code == 200:
            raw_text = response.text
            
            if "<!DOCTYPE html" in raw_text or "redirect_form" in raw_text:
                 return jsonify({"error": "Bloqueo de seguridad"}), 403

            if "JSON**" in raw_text:
                json_part = raw_text.split("JSON**")[1]
                
                # Extraer altura y hora del Pilote para guardar en histórico
                try:
                    data_json = json.loads(json_part)
                    
                    if 'tide' in data_json and 'latest' in data_json['tide']:
                        tide_html = data_json['tide']['latest']
                        tide_html_decoded = tide_html.replace('+', ' ')
                        
                        # Buscar tabla en el HTML
                        table_match = re.search(r'<table[^>]*>(.*?)</table>', tide_html_decoded, re.DOTALL | re.IGNORECASE)
                        if table_match:
                            table_content = table_match.group(1)
                            
                            # Buscar la última fila
                            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_content, re.DOTALL | re.IGNORECASE)
                            if rows:
                                last_row = rows[-1]
                                
                                # Extraer celdas
                                cells = re.findall(r'<td[^>]*>(.*?)</td>', last_row, re.DOTALL | re.IGNORECASE)
                                if len(cells) >= 2:
                                    # Limpiar HTML de la hora
                                    hora_pilote = re.sub(r'<[^>]+>', '', cells[0]).strip()
                                    # Limpiar HTML de la altura
                                    altura_pilote_text = re.sub(r'<[^>]+>', '', cells[1]).strip()
                                    
                                    # Extraer número de la altura (ej: "1.25 m" -> 1.25)
                                    altura_match = re.search(r'([\d\.]+)', altura_pilote_text)
                                    if altura_match:
                                        altura_pilote = float(altura_match.group(1))
                                        # Guardar en histórico
                                        guardar_altura_pilote_en_historico(altura_pilote, hora_pilote)
                                        print(f"📝 Pilote guardado: {altura_pilote}m a las {hora_pilote}")
                except Exception as e:
                    print(f"Error extrayendo datos Pilote: {e}")
                    # No interrumpir el flujo si falla la extracción
                
                return json_part, 200, {'Content-Type': 'application/json'}
            else:
                return jsonify({"error": "Formato inesperado"}), 500
        else:
            return jsonify({"error": f"Error remoto: {response.status_code}"}), 502

    except Exception as e:
        print(f"ERROR: {e}")
        return jsonify({"error": str(e)}), 500

# --- 9. SUDESTADA Y PREDICCIÓN TIGRE ---
@app.route("/api/sudestada")
def get_sudestada():
    """Devuelve información sobre la sudestada actual y predicción para Tigre"""
    try:
        if not os.path.exists(SUDESTADA_FILE):
            return jsonify({
                "activa": False,
                "mensaje": "No hay sudestada activa"
            })
        
        with open(SUDESTADA_FILE, 'r', encoding='utf-8') as f:
            sudestada = json.load(f)
        
        if not sudestada["activa"]:
            return jsonify({
                "activa": False,
                "mensaje": "No hay sudestada activa"
            })
        
        hora_pico = sudestada["hora_pico"]
        altura_pico = sudestada["pico_maximo"]
        
        # Calcular hora para Tigre (+3.5 horas)
        hora_tigre = "No disponible"
        try:
            # Intentar diferentes formatos de hora
            hora_limpia = hora_pico.split()[0]  # Tomar solo la parte de la hora si hay más texto
            
            # Probar diferentes formatos
            formatos = ["%H:%M", "%H:%M:%S", "%H.%M", "%I:%M %p", "%I:%M%p"]
            
            for formato in formatos:
                try:
                    hora_obj = datetime.strptime(hora_limpia, formato)
                    hora_tigre_obj = hora_obj + timedelta(hours=3, minutes=30)
                    hora_tigre = hora_tigre_obj.strftime("%H:%M")
                    break
                except ValueError:
                    continue
            
            # Si no se pudo parsear, mostrar formato aproximado
            if hora_tigre == "No disponible":
                hora_tigre = f"~{hora_limpia} + 3.5h"
                
        except Exception as e:
            print(f"Error calculando hora Tigre: {e}")
            hora_tigre = f"~{hora_pico} + 3.5h"
        
        # Calcular altura para Tigre (+35cm)
        altura_tigre_estimada = round(altura_pico + 0.35, 2)
        
        return jsonify({
            "activa": True,
            "pico_maximo": altura_pico,
            "hora_pico": hora_pico,
            "altura_tigre_estimada": altura_tigre_estimada,
            "hora_tigre_estimada": hora_tigre,
            "mensaje": f"Pico detectado: {altura_pico}m a las {hora_pico}",
            "prediccion_tigre": f"Tigre: ~{altura_tigre_estimada}m para las {hora_tigre}"
        })
        
    except Exception as e:
        return jsonify({"error": str(e)})

# --- 10. INICIALIZACIÓN ---
if __name__ == '__main__':
    # Crear archivos si no existen
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump({"sf": []}, f, ensure_ascii=False, indent=2)
    
    if not os.path.exists(PILOTE_HISTORY_FILE):
        with open(PILOTE_HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump({"registros": []}, f, ensure_ascii=False, indent=2)
    
    if not os.path.exists(SUDESTADA_FILE):
        with open(SUDESTADA_FILE, 'w', encoding='utf-8') as f:
            json.dump({
                "activa": False,
                "pico_maximo": 0,
                "hora_pico": None,
                "timestamp_pico": None,
                "inicio": None
            }, f, ensure_ascii=False, indent=2)
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
