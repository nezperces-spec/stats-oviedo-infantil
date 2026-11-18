import json
import re
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

DURACION_PARTIDO = 80
EQUIPO_OBJETIVO = "REAL OVIEDO"

def limpiar_texto(texto):
    return re.sub(r'\s+', ' ', texto).strip()

def calcular_minutos_jugador(es_titular, minuto_entrada, minuto_salida):
    inicio = 0 if es_titular else minuto_entrada
    fin = minuto_salida if minuto_salida is not None else DURACION_PARTIDO
    if inicio is None:
        return 0
    return max(0, min(DURACION_PARTIDO, fin - inicio))

def extraer_datos_acta(html_acta):
    soup = BeautifulSoup(html_acta, 'html.parser')
    jugadores_partido = {}
    tablas = soup.find_all('table')
    bloque_oviedo = None

    for tabla in tablas:
        texto_tabla = tabla.get_text(" ", strip=True).upper()
        if EQUIPO_OBJETIVO in texto_tabla and ("TITULARES" in texto_tabla or "ALINEACIÓN" in texto_tabla or "DORSAL" in texto_tabla):
            bloque_oviedo = tabla
            break

    if not bloque_oviedo:
        return []

    filas = bloque_oviedo.find_all('tr')
    seccion_actual = "TITULAR"

    for fila in filas:
        texto_fila = limpiar_texto(fila.get_text(" "))
        upper_fila = texto_fila.upper()

        if "SUPLENTE" in upper_fila or "BANQUILLO" in upper_fila:
            seccion_actual = "SUPLENTE"
            continue
        if "TITULAR" in upper_fila:
            seccion_actual = "TITULAR"
            continue
        if "CUERPO TÉCNICO" in upper_fila or "ENTRENADOR" in upper_fila:
            break

        celdas = [limpiar_texto(td.get_text(" ")) for td in fila.find_all(['td', 'th'])]
        if len(celdas) >= 2:
            dorsal = ""
            nombre = ""
            for c in celdas:
                if c.isdigit() and not dorsal:
                    dorsal = c
                elif len(c) > 3 and not c.isdigit() and "DORSAL" not in c.upper() and "JUGADOR" not in c.upper():
                    nombre = c
                    break

            if nombre:
                jugadores_partido[nombre] = {
                    "dorsal": dorsal,
                    "nombre": nombre,
                    "titular": seccion_actual == "TITULAR",
                    "convocado": True,
                    "min_entrada": 0 if seccion_actual == "TITULAR" else None,
                    "min_salida": DURACION_PARTIDO if seccion_actual == "TITULAR" else None,
                    "goles": 0,
                    "amarillas": 0,
                    "rojas": 0
                }

    texto_completo_acta = soup.get_text("\n")
    for linea in texto_completo_acta.split("\n"):
        linea_limpia = limpiar_texto(linea)
        match_min = re.search(r"(\d{1,2})['’]", linea_limpia)
        if match_min:
            minuto = int(match_min.group(1))
            if minuto > DURACION_PARTIDO:
                minuto = DURACION_PARTIDO

            for nombre_jug, datos_jug in jugadores_partido.items():
                partes_nombre = [p for p in nombre_jug.replace(",", "").split() if len(p) > 2]
                if any(p.upper() in linea_limpia.upper() for p in partes_nombre):
                    upper_l = linea_limpia.upper()
                    if "ENTRA" in upper_l or "SUSTITUYE A" in upper_l:
                        datos_jug["min_entrada"] = minuto
                        datos_jug["min_salida"] = DURACION_PARTIDO
                    elif "SALE" in upper_l or "SUSTITUIDO" in upper_l:
                        datos_jug["min_salida"] = minuto
                    elif "GOL" in upper_l or "1-" in linea_limpia or "0-" in linea_limpia:
                        if "TARJETA" not in upper_l:
                            datos_jug["goles"] += 1

    resultado_jugadores = []
    for nombre, d in jugadores_partido.items():
        mins = calcular_minutos_jugador(d["titular"], d["min_entrada"], d["min_salida"])
        d["minutos"] = mins
        d["jugado"] = mins > 0 or d["titular"]
        resultado_jugadores.append(d)

    return resultado_jugadores

def ejecutar_scraper():
    datos_globales = {
        "ultima_actualizacion": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "equipo": "Real Oviedo",
        "competicion": "Segunda Infantil - Grupo 2",
        "duracion_partido": DURACION_PARTIDO,
        "partidos": [],
        "plantilla": {}
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        try:
            page.goto("https://www.asturfutbol.es/pnfg/NPcd/NFG_CmpJornada?cod_primaria=1000120", timeout=60000)
            page.wait_for_timeout(4000)

            filas = page.locator("tr").all()
            for fila in filas:
                texto_fila = limpiar_texto(fila.inner_text())
                if EQUIPO_OBJETIVO in texto_fila.upper():
                    enlace_acta = fila.locator("a[href*='NFG_CmpPartido'], a[href*='acta'], a[title*='Acta']").first
                    if enlace_acta.count() > 0:
                        url_acta = enlace_acta.get_attribute("href")
                        if not url_acta.startswith("http"):
                            url_acta = "https://www.asturfutbol.es" + ("" if url_acta.startswith("/") else "/pnfg/NPcd/") + url_acta

                        page_acta = context.new_page()
                        try:
                            page_acta.goto(url_acta, timeout=30000)
                            page_acta.wait_for_timeout(2000)
                            html_acta = page_acta.content()
                            page_acta.close()

                            jugadores = extraer_datos_acta(html_acta)
                            if jugadores:
                                datos_globales["partidos"].append({
                                    "jornada": "Jornada Actual",
                                    "resumen": texto_fila,
                                    "jugadores": jugadores
                                })
                        except:
                            if not page_acta.is_closed():
                                page_acta.close()
                    break

        except Exception as e:
            print(f"Aviso en la ejecución: {e}")
        finally:
            browser.close()

    for partido in datos_globales["partidos"]:
        for j in partido["jugadores"]:
            nombre = j["nombre"]
            if nombre not in datos_globales["plantilla"]:
                datos_globales["plantilla"][nombre] = {
                    "dorsal": j["dorsal"],
                    "nombre": nombre,
                    "convocados": 0,
                    "jugados": 0,
                    "titular": 0,
                    "suplente": 0,
                    "minutos": 0,
                    "goles": 0,
                    "amarillas": 0,
                    "rojas": 0
                }
            p = datos_globales["plantilla"][nombre]
            if j["dorsal"]:
                p["dorsal"] = j["dorsal"]
            p["convocados"] += 1
            if j["jugado"]:
                p["jugados"] += 1
            if j["titular"]:
                p["titular"] += 1
            elif j["jugado"]:
                p["suplente"] += 1
            p["minutos"] += j["minutos"]
            p["goles"] += j["goles"]

    with open("datos.json", "w", encoding="utf-8") as f:
        json.dump(datos_globales, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    ejecutar_scraper()
