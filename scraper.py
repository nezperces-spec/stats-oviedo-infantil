import json
import re
import os
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

DURACION_PARTIDO = 80
EQUIPO_OBJETIVO = "REAL OVIEDO"
COMPETICION_OBJETIVO = "SEGUNDA INFANTIL"
GRUPO_OBJETIVO = "GRUPO 2"

def limpiar_texto(texto):
    return re.sub(r'\s+', ' ', texto).strip()

def calcular_minutos_jugador(es_titular, minuto_entrada, minuto_salida):
    inicio = 0 if es_titular else minuto_entrada
    fin = minuto_salida if minuto_salida is not None else DURACION_PARTIDO
    if inicio is None:
        return 0
    return max(0, min(DURACION_PARTIDO, fin - inicio))

def extraer_datos_acta(html_acta, jornada, fecha, rival, resultado, es_local):
    soup = BeautifulSoup(html_acta, 'html.parser')
    jugadores_partido = {}

    # Identificar qué bloque corresponde al Real Oviedo (Local o Visitante)
    tablas = soup.find_all('table')
    bloque_oviedo = None

    for tabla in tablas:
        texto_tabla = tabla.get_text(" ", strip=True).upper()
        if EQUIPO_OBJETIVO in texto_tabla and ("TITULARES" in texto_tabla or "ALINEACIÓN" in texto_tabla or "SUPLENTES" in texto_tabla):
            bloque_oviedo = tabla
            break

    if not bloque_oviedo:
        # Búsqueda alternativa por contenedores de equipo en Novanet
        contenedores = soup.find_all(['div', 'section'])
        for cont in contenedores:
            cabecera = cont.find(['h2', 'h3', 'h4', 'strong', 'span'])
            if cabecera and EQUIPO_OBJETIVO in cabecera.get_text().upper():
                if "TITULAR" in cont.get_text().upper() or "DORSAL" in cont.get_text().upper():
                    bloque_oviedo = cont
                    break

    if not bloque_oviedo:
        return []

    # Extraer titulares y suplentes
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
        if "CUERPO TÉCNICO" in upper_fila or "ENTRENADOR" in upper_fila or "DELEGADO" in upper_fila:
            break

        celdas = [limpiar_texto(td.get_text(" ")) for td in fila.find_all(['td', 'th'])]
        if len(celdas) >= 2:
            # Buscar dorsal (número) y nombre del jugador
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

    # Extraer sustituciones, goles y tarjetas del acta completa
    texto_completo_acta = soup.get_text("\n")
    lineas = [limpiar_texto(l) for l in texto_completo_acta.split("\n") if limpiar_texto(l)]

    for i, linea in enumerate(lineas):
        # Detectar sustituciones: formatos habituales Novanet "(45') Entra: X / Sale: Y" o similares
        match_min = re.search(r"(\d{1,2})['’]", linea)
        if match_min:
            minuto = int(match_min.group(1))
            if minuto > DURACION_PARTIDO:
                minuto = DURACION_PARTIDO

            for nombre_jug, datos_jug in jugadores_partido.items():
                # Comprobar coincidencia de apellido/nombre en la línea del evento
                partes_nombre = [p for p in nombre_jug.replace(",", "").split() if len(p) > 2]
                coincide = sum(1 for p in partes_nombre if p.upper() in linea.upper()) >= min(2, len(partes_nombre))

                if coincide:
                    upper_l = linea.upper()
                    if "ENTRA" in upper_l or "SUSTITUYE A" in upper_l:
                        datos_jug["min_entrada"] = minuto
                        datos_jug["min_salida"] = DURACION_PARTIDO
                    elif "SALE" in upper_l or "SUSTITUIDO" in upper_l:
                        datos_jug["min_salida"] = minuto
                    elif "GOL" in upper_l or "1-" in linea or "0-" in linea or "2-" in linea or "3-" in linea:
                        if "TARJETA" not in upper_l and "AMARILLA" not in upper_l:
                            datos_jug["goles"] += 1
                    elif "AMARILLA" in upper_l:
                        datos_jug["amarillas"] += 1
                    elif "ROJA" in upper_l:
                        datos_jug["rojas"] += 1

    # Calcular minutos finales por jugador
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
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        try:
            page.goto("https://www.asturfutbol.es/pnfg/NPcd/NFG_CmpJornada?cod_primaria=1000120", timeout=60000)
            page.wait_for_timeout(3000)

            # Seleccionar Segunda Infantil -> Grupo 2 en los desplegables de Novanet
            selects = page.locator("select").all()
            for sel in selects:
                opciones = sel.locator("option").all_inner_texts()
                for idx, op in enumerate(opciones):
                    if COMPETICION_OBJETIVO in op.upper():
                        sel.select_option(index=idx)
                        page.wait_for_timeout(2000)
                        break

            selects = page.locator("select").all()
            for sel in selects:
                opciones = sel.locator("option").all_inner_texts()
                for idx, op in enumerate(opciones):
                    if GRUPO_OBJETIVO in op.upper():
                        sel.select_option(index=idx)
                        page.wait_for_timeout(2000)
                        break

            # Buscar selector de jornadas y recorrer todas las disputadas
            select_jornada = None
            for sel in page.locator("select").all():
                opciones = sel.locator("option").all_inner_texts()
                if any("JORNADA" in op.upper() for op in opciones):
                    select_jornada = sel
                    break

            num_jornadas = select_jornada.locator("option").count() if select_jornada else 1

            for j_idx in range(num_jornadas):
                if select_jornada:
                    nombre_jornada = limpiar_texto(select_jornada.locator("option").nth(j_idx).inner_text())
                    select_jornada.select_option(index=j_idx)
                    page.wait_for_timeout(2000)
                else:
                    nombre_jornada = f"Jornada {j_idx + 1}"

                # Buscar la fila del partido del Real Oviedo
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
                            page_acta.goto(url_acta, timeout=45000)
                            page_acta.wait_for_timeout(2000)
                            html_acta = page_acta.content()
                            page_acta.close()

                            jugadores = extraer_datos_acta(
                                html_acta,
                                nombre_jornada,
                                "",
                                texto_fila,
                                "",
                                True
                            )

                            if jugadores:
                                datos_globales["partidos"].append({
                                    "jornada": nombre_jornada,
                                    "resumen": texto_fila,
                                    "jugadores": jugadores
                                })
                        break

        except Exception as e:
            print(f"Error durante la extracción: {e}")
        finally:
            browser.close()

    # Consolidar estadísticas totales de la plantilla
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
            p["amarillas"] += j["amarillas"]
            p["rojas"] += j["rojas"]

    with open("datos.json", "w", encoding="utf-8") as f:
        json.dump(datos_globales, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    ejecutar_scraper()

