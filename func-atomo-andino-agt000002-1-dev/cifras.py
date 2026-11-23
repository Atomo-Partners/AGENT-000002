"""
Lógica de la Function preperfil/cifras (AGENT-000002, Atomo Partners).

Recibe el texto que devuelve Document Intelligence (prebuilt-layout, formato markdown)
para los reportes de Supersociedades, y devuelve:
  - las cifras por concepto y año, en pesos y en dólares, con su ubicación;
  - el EBITDA por año (EBIT + depreciación y amortización) y los márgenes EBIT y EBITDA;
  - la evaluación del EBITDA frente al piso en dólares, con todos los años (basta con que uno cumpla);
  - las TRM usadas, que llegan en la solicitud desde trm.json (validadas por el equipo);
  - alertas de todo lo que no se pudo leer o calcular.

Aquí no hay ningún modelo de lenguaje: todo es determinístico.
"""

import datetime as dt
import os
import re
import unicodedata
from html.parser import HTMLParser

VERSION = "1.2"

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

MARGEN_CERCANO = float(os.environ.get("MARGEN_CERCANO", "0.10"))

FACTOR_ESCALA = {"unidades": 1, "miles": 1_000, "millones": 1_000_000, "miles_de_millones": 1_000_000_000}

# El orden importa: "otro resultado integral" se revisa antes que "resultado integral".
ESTADOS = [
    ("otro_resultado_integral", "otro resultado integral"),
    ("resultado_integral", "resultado integral"),
    ("situacion_financiera", "situacion financiera"),
    ("flujo_efectivo", "flujo de efectivo"),
    ("cambios_patrimonio", "cambios en el patrimonio"),
]

# Los saldos (balance) se convierten con la TRM de cierre; los flujos, con la TRM promedio.
TASA_POR_ESTADO = {"situacion_financiera": "cierre"}

# Etiqueta normalizada (sin tildes, minúsculas, sin el signo inicial) -> concepto.
CONCEPTOS = {
    "resultado_integral": {
        "ingresos de actividades ordinarias": "ingresos",
        "costo de ventas": "costo_ventas",
        "ganancia bruta": "ganancia_bruta",
        "gastos de ventas": "gastos_ventas",
        "gastos de administracion": "gastos_administracion",
        "ganancia (perdida) por actividades de operacion": "ebit",
        "ingresos financieros": "ingresos_financieros",
        "costos financieros": "costos_financieros",
        "ganancia (perdida), antes de impuestos": "utilidad_antes_impuestos",
        "ingreso (gasto) por impuestos": "impuestos",
        "ganancia (perdida)": "utilidad_neta",
    },
    "flujo_efectivo": {
        "ajustes por gastos de depreciacion y amortizacion": "depreciacion_amortizacion",
        "flujos de efectivo netos procedentes de (utilizados en) actividades de operacion": "flujo_operacion",
        "flujos de efectivo netos procedentes de (utilizados en) actividades de inversion": "flujo_inversion",
        "flujos de efectivo netos procedentes de (utilizados en) actividades de financiacion": "flujo_financiacion",
        "compras de propiedades, planta y equipo": "compras_ppe",
        "dividendos pagados": "dividendos_pagados",
    },
    "situacion_financiera": {
        "efectivo y equivalentes al efectivo": "efectivo",
        "activos corrientes totales": "activos_corrientes",
        "propiedades, planta y equipo": "propiedades_planta_equipo",
        "total de activos": "activos",
        "pasivos corrientes totales": "pasivos_corrientes",
        "otros pasivos financieros corrientes": "pasivos_financieros_corrientes",
        "otros pasivos financieros no corrientes": "pasivos_financieros_no_corrientes",
        "total pasivos": "pasivos",
        "patrimonio total": "patrimonio",
    },
    "otro_resultado_integral": {
        "total otro resultado integral": "otro_resultado_integral",
        "resultado integral total": "resultado_integral_total",
    },
}

# Conceptos que deberían aparecer si el estado se leyó completo. Si faltan, se alerta:
# suele significar que Document Intelligence no leyó todas las páginas.
ESPERADOS = {
    "resultado_integral": ["ingresos", "ebit", "utilidad_neta"],
    "flujo_efectivo": ["depreciacion_amortizacion", "flujo_operacion", "flujo_financiacion"],
    "situacion_financiera": ["activos", "pasivos", "patrimonio"],
    "otro_resultado_integral": ["resultado_integral_total"],
}


# ---------------------------------------------------------------------------
# Utilidades de texto y números
# ---------------------------------------------------------------------------

def sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def normalizar_etiqueta(texto: str) -> str:
    t = sin_tildes(texto or "").lower()
    t = re.sub(r"(\w)- (\w)", r"\1\2", t)          # "dete- rioro" -> "deterioro"
    t = re.sub(r"^\(\s*[+\-/ ]+\s*\)\s*", "", t)   # quita "(+/-) ", "( + ) ", "(-) "
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def leer_valor(celda: str):
    """Convierte '$1.234.567 M', '$-94.749 M', '$0 M', '-' o '$NaN M'.
    Devuelve (valor, problema). '-' y vacío son 'sin dato' (None), no cero."""
    s = (celda or "").strip()
    if s in ("", "-", "—", "–"):
        return None, None
    if "nan" in s.lower():
        return None, "valor NaN"
    negativo = False
    if s.startswith("(") and s.endswith(")"):
        negativo, s = True, s[1:-1]
    m = re.fullmatch(r"\$?\s*(-)?\s*\$?\s*([\d.]+(?:,\d+)?)\s*(?:M|MM)?", s)
    if not m:
        return None, f"no numérico: '{celda}'"
    if m.group(1):
        negativo = True
    numero = float(m.group(2).replace(".", "").replace(",", "."))
    if numero.is_integer():
        numero = int(numero)
    return (-numero if negativo else numero), None


def formato_numero(valor, decimales=0):
    """1234567.891 -> '1.234.567,89' (formato colombiano)."""
    texto = f"{valor:,.{decimales}f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def texto_millones(valor, moneda):
    """Texto listo para mostrar: 'COP 134.537 millones' o 'USD 33,20 millones'.
    Lo calcula el código para que ningún agente tenga que hacer cuentas ni cambiar unidades."""
    if valor is None:
        return None
    decimales = 0 if moneda == "COP" else 2
    return f"{moneda} {formato_numero(valor / 1_000_000, decimales)} millones"


def texto_pct(valor):
    return None if valor is None else f"{formato_numero(valor, 1)}%"


# ---------------------------------------------------------------------------
# Lectura de tablas del markdown de Document Intelligence
# ---------------------------------------------------------------------------

class _LectorTabla(HTMLParser):
    """Lee una tabla <table><tr><th|td> del markdown de Document Intelligence."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.filas, self._fila, self._celda = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._fila = []
        elif tag in ("td", "th") and self._fila is not None:
            self._celda = {"texto": "", "encabezado": tag == "th"}

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._celda is not None:
            self._celda["texto"] = re.sub(r"\s+", " ", self._celda["texto"]).strip()
            self._fila.append(self._celda)
            self._celda = None
        elif tag == "tr" and self._fila is not None:
            self.filas.append(self._fila)
            self._fila = None

    def handle_data(self, data):
        if self._celda is not None:
            self._celda["texto"] += data


def leer_tablas(contenido: str):
    """Devuelve las tablas del documento con su número de página."""
    tablas = []
    for m in re.finditer(r"<table>.*?</table>", contenido, flags=re.S):
        pagina = 1 + contenido.count("<!-- PageBreak -->", 0, m.start())
        lector = _LectorTabla()
        lector.feed(m.group(0))
        tablas.append({"pagina": pagina, "filas": lector.filas})
    return tablas


def detectar_estado(contenido: str):
    titulo = ""
    m = re.search(r"^#\s*(.+)$", contenido, flags=re.M)
    if m:
        titulo = m.group(1).strip()
    base = normalizar_etiqueta(titulo or contenido[:500])
    for clave, patron in ESTADOS:
        if patron in base:
            return clave, titulo
    return "otro", titulo


def detectar_nit(contenido: str):
    m = re.search(r"NIT\s*[:\n ]+\s*([\d.\-]+)", contenido)
    return m.group(1).replace(".", "") if m else None


# ---------------------------------------------------------------------------
# Proceso principal
# ---------------------------------------------------------------------------

def procesar(cuerpo: dict, hoy: dt.date | None = None) -> dict:
    hoy = hoy or dt.date.today()
    alertas = []
    escala = cuerpo.get("escala_supersociedades", "miles")
    factor = FACTOR_ESCALA.get(escala)
    if factor is None:
        raise ValueError(f"escala no reconocida: {escala}")
    piso = cuerpo.get("piso_ebitda_usd")
    # La TRM ya no se consulta en internet: llega en la solicitud, con el contenido de trm.json,
    # el archivo que el equipo genera y valida. Acepta {"anios": {...}} o directamente {"2025": {...}}.
    trm_archivo = cuerpo.get("trm") or {}
    trm_por_anio = trm_archivo.get("anios", trm_archivo) if isinstance(trm_archivo, dict) else {}
    fuente_trm = trm_archivo.get("fuente", "trm.json") if isinstance(trm_archivo, dict) else "trm.json"
    if not trm_por_anio:
        alertas.append("La solicitud no trae TRM (trm.json vacío o no enviado): ninguna cifra queda en dólares.")

    # 1. Leer cada documento --------------------------------------------------
    estados_leidos, nits, encontrados = [], set(), {}
    for doc in cuerpo.get("documentos", []):
        did, nombre = doc.get("id"), doc.get("nombre")
        contenido = doc.get("texto") or ""
        if doc.get("estado") not in (None, "succeeded") or not contenido.strip():
            alertas.append(f"{did} ({nombre}): no utilizable (estado {doc.get('estado')} o sin texto).")
            continue

        estado, titulo = detectar_estado(contenido)
        nit = detectar_nit(contenido)
        if nit:
            nits.add(nit)
        tablas = leer_tablas(contenido)
        estados_leidos.append({"documento": did, "nombre": nombre, "estado": estado, "titulo": titulo,
                               "nit": nit, "tablas": len(tablas),
                               "paginas_leidas": 1 + contenido.count("<!-- PageBreak -->")})
        if estado == "otro":
            alertas.append(f"{did} ({nombre}): no se reconoció el tipo de estado ('{titulo}').")
            continue
        if not tablas:
            alertas.append(f"{did} ({nombre}): no se encontraron tablas.")
            continue

        # Cada reporte trae el año del reporte y, a veces, el año anterior como comparativo.
        # Solo se usa la columna del año del reporte (el más reciente del documento), para que
        # ningún año aparezca dos veces y no se mezclen cifras de reportes distintos.
        anios_doc = {f.group(1) for t in tablas if t["filas"]
                     for c in t["filas"][0][1:] for f in [re.search(r"(\d{4})-(\d{2})-(\d{2})", c["texto"])] if f}
        anio_reporte = max(anios_doc) if anios_doc else None
        estados_leidos[-1]["anio_reporte"] = anio_reporte
        estados_leidos[-1]["anios_comparativos_ignorados"] = sorted(anios_doc - {anio_reporte}, reverse=True)

        mapa = CONCEPTOS.get(estado, {})
        columnas_previas, ancho_previo = None, None
        for tabla in tablas:
            filas = tabla["filas"]
            if not filas:
                continue
            tiene_fechas = any(re.search(r"\d{4}-\d{2}-\d{2}", c["texto"]) for c in filas[0][1:])
            if tiene_fechas:
                columnas = {}
                for i, celda in enumerate(filas[0][1:], start=1):
                    f = re.search(r"(\d{4})-(\d{2})-(\d{2})", celda["texto"])
                    if f and f.group(1) == anio_reporte:
                        columnas[i] = {"anio": f.group(1), "fecha_corte": f.group(0)}
                columnas_previas, ancho_previo = columnas, len(filas[0])
                filas_datos = filas[1:]
            elif columnas_previas and len(filas[0]) == ancho_previo:
                # Continuación de una tabla en otra página, sin la fila de fechas: usa las columnas
                # de la tabla anterior del mismo documento, que en Supersociedades tiene la misma estructura.
                columnas, filas_datos = columnas_previas, filas
            else:
                # Tabla sin fechas y sin tabla anterior (por ejemplo, el recuadro del NIT).
                # Solo se avisa si trae cifras, porque entonces sí se están perdiendo datos.
                if any("$" in c["texto"] for fila in filas for c in fila[1:]):
                    alertas.append(f"{did}: tabla de la página {tabla['pagina']} con cifras pero sin encabezado "
                                   f"de fechas; se omitió.")
                continue
            if not columnas:
                continue
            for fila in filas_datos:
                if not fila:
                    continue
                etiqueta = fila[0]["texto"]
                concepto = mapa.get(normalizar_etiqueta(etiqueta))
                for i, col in columnas.items():
                    texto = fila[i]["texto"] if i < len(fila) else ""
                    valor, problema = leer_valor(texto)
                    if problema:
                        alertas.append(f"{did}, página {tabla['pagina']}, renglón '{etiqueta or '(sin nombre)'}', "
                                       f"{col['anio']}: {problema}; se trató como vacío.")
                    if concepto is None or valor is None:
                        continue
                    clave = (concepto, col["anio"])
                    previo = encontrados.get(clave)
                    if previo and previo["valor_original"] != valor:
                        alertas.append(f"{concepto} {col['anio']} aparece dos veces con valores distintos "
                                       f"({previo['valor_original']} y {valor}); se usó el último.")
                    encontrados[clave] = {
                        "concepto": concepto, "estado": estado, "anio": col["anio"],
                        "fecha_corte": col["fecha_corte"], "valor_original": valor, "escala": escala,
                        "moneda": "COP", "valor_cop": valor * factor,
                        "tipo_tasa": TASA_POR_ESTADO.get(estado, "promedio"),
                        "documento": did, "pagina": tabla["pagina"], "renglon": etiqueta,
                    }

        conceptos_doc = {c for (c, _a), v in encontrados.items() if v["documento"] == did}
        faltan = [c for c in ESPERADOS.get(estado, []) if c not in conceptos_doc]
        if faltan:
            alertas.append(f"{did} ({nombre}): faltan {', '.join(faltan)}. Revise si Document Intelligence "
                           f"leyó todas las páginas.")

    if len(nits) > 1:
        alertas.append(f"Los documentos traen NIT distintos: {', '.join(sorted(nits))}.")

    # 2. Deuda financiera (derivada) ---------------------------------------------
    anios = sorted({a for (_c, a) in encontrados}, reverse=True)
    for a in anios:
        c, nc = encontrados.get(("pasivos_financieros_corrientes", a)), encontrados.get(("pasivos_financieros_no_corrientes", a))
        if c and nc:
            encontrados[("deuda_financiera", a)] = {
                **nc, "concepto": "deuda_financiera",
                "valor_original": c["valor_original"] + nc["valor_original"],
                "valor_cop": c["valor_cop"] + nc["valor_cop"],
                "renglon": "Otros pasivos financieros corrientes + no corrientes", "derivado": True,
            }

    # 3. TRM (desde trm.json) -------------------------------------------------
    trm = {}
    for a in anios:
        datos = trm_por_anio.get(a) if isinstance(trm_por_anio, dict) else None
        if not datos or not datos.get("promedio"):
            alertas.append(f"La TRM de {a} no está en trm.json: las cifras de {a} quedan sin dólares. "
                           f"Agrega ese año al archivo y repite la corrida.")
            continue
        trm[a] = {"promedio": datos.get("promedio"), "cierre": datos.get("cierre"),
                  "fuente": datos.get("fuente", fuente_trm)}
        if not datos.get("cierre"):
            alertas.append(f"trm.json no trae TRM de cierre para {a}: los saldos del balance de {a} quedan sin dólares.")

    def a_usd(valor_cop, anio, tipo):
        tasa = (trm.get(anio) or {}).get(tipo)
        return (round(valor_cop / tasa, 2), tasa) if tasa else (None, None)

    cifras = []
    for v in sorted(encontrados.values(), key=lambda x: (x["concepto"], x["anio"])):
        usd, tasa = a_usd(v["valor_cop"], v["anio"], v["tipo_tasa"])
        cifras.append({**v, "trm": tasa, "valor_usd": usd})

    # 4. EBITDA por año ---------------------------------------------------------
    ebitda = []
    for a in sorted({a for (c, a) in encontrados if c == "ebit"}, reverse=True):
        ebit = encontrados[("ebit", a)]
        da = encontrados.get(("depreciacion_amortizacion", a))
        ingresos = encontrados.get(("ingresos", a))
        ingresos_cop = ingresos["valor_cop"] if ingresos else None

        def margen(valor):
            return round(valor / ingresos_cop * 100, 1) if ingresos_cop else None

        ingresos_usd, _ = a_usd(ingresos_cop, a, "promedio") if ingresos_cop else (None, None)
        ebit_usd, _ = a_usd(ebit["valor_cop"], a, "promedio")

        if not da:
            ebitda.append({"anio": a, "estado": "no_determinable", "ebit_cop": ebit["valor_cop"],
                           "ingresos_cop": ingresos_cop, "margen_ebit_pct": margen(ebit["valor_cop"]),
                           "margen_ebitda_pct": None,
                           "motivo": f"No hay depreciación y amortización de {a}: falta el flujo de efectivo de ese año.",
                           "textos": {
                               "ventas_cop": texto_millones(ingresos_cop, "COP"),
                               "ventas_usd": texto_millones(ingresos_usd, "USD"),
                               "ebit_cop": texto_millones(ebit["valor_cop"], "COP"),
                               "ebit_usd": texto_millones(ebit_usd, "USD"),
                               "ebitda_cop": "No determinable", "ebitda_usd": "No determinable",
                               "margen_ebit": texto_pct(margen(ebit["valor_cop"])),
                               "margen_ebitda": "No determinable",
                           }})
            continue
        da_cop = da["valor_cop"]
        if da_cop < 0:
            alertas.append(f"La depreciación y amortización de {a} viene negativa; se usó su valor absoluto.")
            da_cop = abs(da_cop)
        total = ebit["valor_cop"] + da_cop
        usd, tasa = a_usd(total, a, "promedio")
        ebitda.append({
            "anio": a, "estado": "calculado", "formula": "ebit + depreciacion_amortizacion",
            "ebit_cop": ebit["valor_cop"], "depreciacion_amortizacion_cop": da_cop, "ebitda_cop": total,
            "trm_promedio": tasa, "ebitda_usd": usd,
            "ingresos_cop": ingresos_cop, "margen_ebit_pct": margen(ebit["valor_cop"]),
            "margen_ebitda_pct": margen(total),
            "textos": {
                "ventas_cop": texto_millones(ingresos_cop, "COP"),
                "ventas_usd": texto_millones(ingresos_usd, "USD"),
                "ebit_cop": texto_millones(ebit["valor_cop"], "COP"),
                "ebit_usd": texto_millones(ebit_usd, "USD"),
                "ebitda_cop": texto_millones(total, "COP"),
                "ebitda_usd": texto_millones(usd, "USD"),
                "margen_ebit": texto_pct(margen(ebit["valor_cop"])),
                "margen_ebitda": texto_pct(margen(total)),
                "trm_promedio": formato_numero(tasa, 2) if tasa else None,
            },
            "fuentes": [{"concepto": "ebit", "documento": ebit["documento"], "pagina": ebit["pagina"], "renglon": ebit["renglon"]},
                        {"concepto": "depreciacion_amortizacion", "documento": da["documento"], "pagina": da["pagina"], "renglon": da["renglon"]}],
        })

    # 5. Evaluación del piso de EBITDA -----------------------------------------
    # Regla de Atomo: basta con que un año con EBITDA calculado cumpla el piso.
    #   cumple          todos los años calculados cumplen
    #   cumple_parcial  al menos uno cumple y otro no (se informa como alerta)
    #   no_cumple       ninguno cumple; cercano_al_piso dice si el mejor está a menos de MARGEN_CERCANO
    #   sin_trm         hay EBITDA en pesos, pero ningún año evaluable tiene TRM en trm.json
    #   no_determinable no hay ningún año con EBITDA calculado
    evaluacion = {
        "piso_usd": piso,
        "regla": "Cumple si al menos un año con EBITDA calculado está en el piso o por encima.",
        "resultado": "no_determinable",
        "anios_evaluados": [], "anios_cumplen": [], "anios_no_cumplen": [],
        "anios_no_determinables": [e["anio"] for e in ebitda if e["estado"] != "calculado"],
        "anios_sin_trm": [],
        "mejor_anio": None, "mejor_ebitda_usd": None,
        "cercano_al_piso": False, "detalle": [], "alerta": "", "motivo": "",
        "textos": {"piso_usd": texto_millones(piso, "USD") if piso is not None else None},
    }
    evaluables = [e for e in ebitda if e["estado"] == "calculado" and e.get("ebitda_usd") is not None]
    for e in ebitda:
        if e["estado"] == "calculado" and e.get("ebitda_usd") is None:
            evaluacion["anios_sin_trm"].append(e["anio"])

    if piso is None:
        evaluacion["motivo"] = "No llegó piso_ebitda_usd."
    elif not evaluables and evaluacion["anios_sin_trm"]:
        evaluacion["resultado"] = "sin_trm"
        evaluacion["motivo"] = (f"Hay EBITDA calculado en pesos para {', '.join(evaluacion['anios_sin_trm'])}, "
                                f"pero esos años no están en trm.json. Hay que agregarlos y repetir la corrida.")
    elif not evaluables:
        evaluacion["motivo"] = "No hay ningún año con EBITDA calculado."
    else:
        for e in evaluables:
            cumple = e["ebitda_usd"] >= piso
            e["cumple_piso"] = cumple
            dif = (e["ebitda_usd"] - piso) / piso
            evaluacion["detalle"].append({
                "anio": e["anio"], "ebitda_usd": e["ebitda_usd"], "cumple": cumple,
                "diferencia_pct": round(dif * 100, 1),
                "textos": {"ebitda_usd": texto_millones(e["ebitda_usd"], "USD"),
                           "diferencia": f"{'+' if dif >= 0 else '-'}{formato_numero(abs(dif) * 100, 1)}%"},
            })
            evaluacion["anios_evaluados"].append(e["anio"])
            evaluacion["anios_cumplen" if cumple else "anios_no_cumplen"].append(e["anio"])

        mejor = max(evaluables, key=lambda e: e["ebitda_usd"])
        evaluacion["mejor_anio"] = mejor["anio"]
        evaluacion["mejor_ebitda_usd"] = mejor["ebitda_usd"]
        si, no = evaluacion["anios_cumplen"], evaluacion["anios_no_cumplen"]
        if si and not no:
            evaluacion["resultado"] = "cumple"
        elif si:
            evaluacion["resultado"] = "cumple_parcial"
            evaluacion["alerta"] = f"El EBITDA cumple el piso en {', '.join(si)} pero no en {', '.join(no)}."
        else:
            evaluacion["resultado"] = "no_cumple"
            evaluacion["cercano_al_piso"] = (piso - mejor["ebitda_usd"]) / piso <= MARGEN_CERCANO
        if evaluacion["anios_sin_trm"]:
            aviso = (f"No se evaluaron {', '.join(evaluacion['anios_sin_trm'])} porque no están en trm.json.")
            evaluacion["alerta"] = (evaluacion["alerta"] + " " + aviso).strip()

    for e in ebitda:
        e.setdefault("cumple_piso", None)

    return {
        "version": VERSION,
        "generado": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "nit_documentos": sorted(nits),
        "escala": escala,
        "estados_leidos": estados_leidos,
        "trm": trm,
        "cifras": cifras,
        "ebitda": ebitda,
        "evaluacion_piso": evaluacion,
        "alertas": alertas,
    }
