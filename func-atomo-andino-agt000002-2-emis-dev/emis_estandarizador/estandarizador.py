"""Estandariza la salida de Document Intelligence (prebuilt-layout) de un reporte EMIS
en un JSON organizado por secciones, guiado por el catálogo de la plantilla EMIS."""

import re
from collections import OrderedDict
from datetime import datetime, timezone

from .catalogo import Catalogo
from .utilidades import anio_de, clave, es_numerico, limpiar, parsear_fecha, parsear_numero, redondear
from .validaciones import validar

VERSION_ESQUEMA = "1.2"

SECCIONES_ESTADOS = [
    "estados_financieros.estado_resultados",
    "estados_financieros.balance_general",
    "estados_financieros.flujo_de_caja",
]
GRUPOS_RATIOS = [
    "rentabilidad", "eficiencia", "cifras_de_valoracion", "liquidez", "apalancamiento",
    "tendencias_de_crecimiento", "cobertura", "desempeno", "tamano_comun",
]
TASA_POR_SECCION = {
    "estados_financieros.estado_resultados": "tasa_resultados",
    "estados_financieros.balance_general": "tasa_balance",
    "estados_financieros.flujo_de_caja": "tasa_flujo",
    "ratios": "tasa_ratios",
}
# Bloques de texto por año, en el orden en que aparecen en el reporte.
BLOQUES = ["estados.detalles", "estados.otra", "ratios.detalles", "ratios.otra"]
ACEPTA = {
    "detalles": {"statement_type", "period_end_date", "provider", "units"},
    "otra": {"statement_type", "begin_date", "period_end_date", "original_units", "source"},
}
CAMPOS_FECHA = {"begin_date", "period_end_date"}
SECCION_CORTA = {
    "estados_financieros.estado_resultados": "resultados",
    "estados_financieros.balance_general": "balance",
    "estados_financieros.flujo_de_caja": "flujo",
}
# Cuentas sin las cuales varios KPIs del catálogo de Átomo quedan sin dato. Cada grupo se cumple
# si existe al menos una de sus cuentas.
CUENTAS_CLAVE = [
    ("ingresos", ["total_operating_revenue", "net_sales_revenue"]),
    ("ebit", ["operating_profit"]),
    ("gastos_financieros", ["finance_expenses"]),
    ("activos_totales", ["total_assets"]),
    ("patrimonio", ["total_equity"]),
    ("caja", ["cash_and_cash_equivalents"]),
    ("deuda_financiera", ["current_loans_and_borrowings", "non_current_loans_and_borrowings"]),
    ("cuentas_por_cobrar", ["current_trade_receivables", "trade_and_other_receivables"]),
    ("flujo_operativo", ["net_cash_flow_operating"]),
    ("compra_ppe", ["purchase_of_ppe"]),
]
_RE_EMPLEADOS = re.compile(r"([\d][\d.,]*)\s*(?:\((\d{4})\))?")


# Bloques de la hoja resumen que llegan como párrafos (ver _leer_bloques_resumen).
MARCAS_HOJA_RESUMEN = ("hojaresumendecompania", "companytearsheet")
BLOQUES_RESUMEN = {
    "ejecutivos": ("principalesejecutivos", "keyexecutives"),
    "accionistas": ("detallesdeaccionistas", "shareholder"),
    "subsidiarias": ("subsidiariasyfiliales", "subsidiaries"),
}
RE_SUFIJO_SOCIETARIO = re.compile(r"^(s\.?a\.?s\.?|s\.?a\.?|inc\.?|ltda\.?|llc|corp\.?|s\.?\s?de\s?r\.?l\.?|bic)$", re.I)


# Cargos que identifican una tabla o un bloque de ejecutivos (se comparan normalizados).
PALABRAS_CARGO = ("representantelegal", "miembro", "juntadirectiva", "revisorfiscal", "gerente", "presidente",
                  "director", "legalrepresentative", "boardmember", "chairman", "ceo", "cfo", "auditor",
                  "secretari", "vicepresident", "manager", "officer")

# Encabezados y pies de página del PDF de EMIS que cortan los bloques de párrafos.
RE_RUIDO_PAGINA = re.compile(r"^(copyright|page \d+ of \d+|p[aá]gina \d+ de \d+|isi emis$|emis business report)", re.I)


class ErrorEntrada(ValueError):
    """La entrada no tiene la estructura esperada."""


def _construir_grilla(tabla):
    filas, columnas = tabla.get("rowCount", 0), tabla.get("columnCount", 0)
    grilla = [["" for _ in range(columnas)] for _ in range(filas)]
    for celda in tabla.get("cells", []):
        f, c = celda.get("rowIndex", 0), celda.get("columnIndex", 0)
        if f < filas and c < columnas:
            grilla[f][c] = limpiar(celda.get("content", ""))
    regiones = tabla.get("boundingRegions") or [{}]
    return grilla, regiones[0].get("pageNumber")


class Estandarizador:
    def __init__(self, catalogo=None):
        self.cat = catalogo or Catalogo.cargar()

    # ------------------------------------------------------------------ API
    def procesar(self, entrada):
        self._validar_entrada(entrada)
        self._reiniciar()
        tablas = entrada["tablas"]
        for indice, tabla in enumerate(tablas):
            grilla, pagina = _construir_grilla(tabla)
            self._procesar_tabla(indice, grilla, pagina)
        self._cerrar_pares()
        if self.tasa_pendiente:
            self._advertir("tasa_sin_seccion", "Hay una fila de tasa de cambio que no se pudo asignar a un estado.")
        self._procesar_parrafos(entrada.get("parrafos"))
        return self._armar_salida(entrada, len(tablas))

    # ------------------------------------------------------------- estado
    def _reiniciar(self):
        self.tipo_anterior = None
        self.ncols_encabezado = None
        self.anios_cols = {}
        self.anios_series = set()
        self.seccion_actual = None
        self.mayor = "estados"
        self.bloque = 0
        self.ultima_fila_titulo = None
        self.tasa_pendiente = None
        self.vistos = []
        self.etiquetas_leidas = []

        self.empresa = OrderedDict()
        self.resumen = OrderedDict()
        self.anios_resumen = []
        self.filas_pares = []
        self.pares = []
        self.series = {s: OrderedDict() for s in SECCIONES_ESTADOS + ["ratios"]}
        self.bloques = {b: OrderedDict() for b in BLOQUES}
        self.nota_ratios = None
        self.tasas = {}
        self.textos = {}
        self.parrafos_sin_clasificar = []
        self.moneda_original_parrafo = None
        self.ejecutivos_tabla = []

        self.correcciones = []
        self.advertencias = []
        self.no_reconocidas = []

    def _advertir(self, codigo, detalle, **extra):
        self.advertencias.append({"codigo": codigo, "detalle": detalle, **extra})

    @staticmethod
    def _validar_entrada(entrada):
        if not isinstance(entrada, dict):
            raise ErrorEntrada("La entrada debe ser un objeto JSON.")
        if not isinstance(entrada.get("tablas"), list) or not entrada["tablas"]:
            raise ErrorEntrada("La entrada debe traer 'tablas' con al menos una tabla de Document Intelligence.")
        for i, t in enumerate(entrada["tablas"]):
            if not isinstance(t, dict) or "cells" not in t:
                raise ErrorEntrada(f"La tabla {i} no tiene 'cells'.")

    # ------------------------------------------------------- clasificación
    def _clasificar(self, grilla):
        celdas = [c for fila in grilla for c in fila if c]
        pares_clave_valor = sum(1 for c in celdas if ":" in c and clave(c.split(":", 1)[0]) in self.cat.indice_empresa)
        if pares_clave_valor >= 2:
            return "empresa"

        filas = [f for f in grilla if any(f)]
        if not filas:
            return "vacia"
        if any(clave(c) in self.cat.indice_texto for c in filas[0] if c) and not any(es_numerico(c) for c in celdas):
            return "texto_columnas"  # 'Descripción de Compañías | Productos Principales' con el texto debajo
        if self._es_tabla_ejecutivos(filas, celdas):
            return "ejecutivos"
        if len(grilla[0]) == 2:
            con_pagina = sum(1 for f in filas if f[1].isdigit() and int(f[1]) < 1000 and not es_numerico(f[0]))
            if con_pagina >= 0.6 * len(filas):
                return "indice"

        for f in filas:
            nombre = self.cat.encabezados.get(clave(f[0]))
            if nombre == "compania":
                return "pares"
            if nombre == "periodo_fiscal":
                return "resumen"
            if nombre == "columna_nombre":
                return "serie"

        claves_filas = [clave(f[0]) for f in filas]
        reconocidas = sum(1 for k in claves_filas if k in self.cat.indice_cuentas or k in self.cat.indice_detalles
                          or k in self.cat.titulos or k in self.cat.encabezados)
        if self.tipo_anterior == "resumen" and any(k in self.cat.indice_resumen for k in claves_filas):
            return "resumen"
        if reconocidas:
            return "serie"
        if self.tipo_anterior == "pares":
            return "pares"
        return "desconocida"

    def _procesar_tabla(self, indice, grilla, pagina):
        tipo = self._clasificar(grilla)
        continua = tipo == self.tipo_anterior and tipo in ("serie", "resumen", "pares")
        if tipo == "empresa":
            self._procesar_empresa(grilla)
        elif tipo == "texto_columnas":
            self._procesar_texto_columnas(grilla)
        elif tipo == "ejecutivos":
            for fila in grilla:
                textos = [c for c in fila if c]
                if len(textos) >= 2:
                    self.ejecutivos_tabla.append(OrderedDict([("nombre", textos[0]), ("cargo", textos[1])]))
        elif tipo == "resumen":
            self._procesar_resumen(indice, grilla, pagina)
        elif tipo == "pares":
            self._procesar_pares(indice, grilla, pagina, continua)
        elif tipo == "serie":
            self._procesar_serie(indice, grilla, pagina)
        elif tipo == "desconocida":
            for r, fila in enumerate(grilla):
                if any(fila[1:]):
                    self.no_reconocidas.append({"etiqueta": fila[0], "contexto": "tabla_desconocida",
                                                "pagina": pagina, "tabla": indice, "fila": r, "valores": fila[1:]})
        if tipo not in ("vacia",):
            self.tipo_anterior = tipo if tipo != "indice" else self.tipo_anterior

    @staticmethod
    def _es_tabla_ejecutivos(filas, celdas):
        if any(es_numerico(c) for c in celdas):
            return False
        con_cargo = sum(1 for f in filas if len([c for c in f if c]) >= 2
                        and any(p in clave([c for c in f if c][1]) for p in PALABRAS_CARGO))
        return con_cargo >= max(1, 0.6 * len(filas))

    def _procesar_texto_columnas(self, grilla):
        """Cada columna: la etiqueta arriba y el texto partido en las filas de abajo."""
        encabezado = grilla[0]
        for c, etiqueta in enumerate(encabezado):
            destino = self.cat.indice_texto.get(clave(etiqueta)) if etiqueta else None
            if not destino:
                continue
            texto = limpiar(" ".join(f[c] for f in grilla[1:] if c < len(f) and f[c]))
            if texto:
                seccion, campo = destino
                self.textos.setdefault(seccion, OrderedDict())[campo] = texto

    # ------------------------------------------------------------ empresa
    def _pares_empresa(self, texto):
        """'Forma Legal: S.A.S. Estatus Operacional: Operacional EMISid: 1186850' -> varios campos.
        Solo aplica si el párrafo empieza con una etiqueta de la ficha de la empresa."""
        if not hasattr(self, "_re_empresa"):
            partes = []
            for campo_id, campo in self.cat.empresa.items():
                for etiquetas in campo["etiquetas"].values():
                    for e in etiquetas:
                        dos_puntos = r"\s*:?" if campo_id == "sede" else r"\s*:"
                        partes.append((len(e), re.escape(e) + dos_puntos, campo_id))
            partes.sort(reverse=True)
            self._re_empresa = re.compile("|".join(f"(?P<g{i}>{patron})" for i, (_, patron, _) in enumerate(partes)),
                                          re.I)
            self._ids_empresa = [campo_id for _, _, campo_id in partes]
        coincidencias = list(self._re_empresa.finditer(texto))
        if not coincidencias or texto[:coincidencias[0].start()].strip():
            return []
        pares = []
        for n, m in enumerate(coincidencias):
            campo_id = self._ids_empresa[int(m.lastgroup[1:])]
            fin = coincidencias[n + 1].start() if n + 1 < len(coincidencias) else len(texto)
            valor = re.sub(r"\s+(Mapa|Map)\s*$", "", texto[m.end():fin].strip())
            pares.append((campo_id, valor))
        return pares

    def _procesar_empresa(self, grilla):
        columnas = len(grilla[0]) if grilla else 0
        for c in range(columnas):
            campo_abierto = None
            for fila in grilla:
                texto = fila[c] if c < len(fila) else ""
                if not texto or clave(texto) in self.cat.titulos:
                    continue
                if ":" in texto and clave(texto.split(":", 1)[0]) in self.cat.indice_empresa:
                    etiqueta, valor = texto.split(":", 1)
                    self._guardar_campo_empresa(self.cat.indice_empresa[clave(etiqueta)], valor)
                    campo_abierto = None
                elif clave(texto) in self.cat.indice_empresa:
                    campo_abierto = self.cat.indice_empresa[clave(texto)]
                elif campo_abierto and clave(texto) not in ("map", "mapa"):
                    previo = self.empresa.get(campo_abierto) or ""
                    self.empresa[campo_abierto] = limpiar(f"{previo} {texto}")

    def _guardar_campo_empresa(self, campo, valor):
        valor = limpiar(valor)
        if self.cat.empresa[campo].get("tipo") == "fecha":
            valor = parsear_fecha(valor, self.cat.meses)
        self.empresa[campo] = valor or None

    # ------------------------------------------------------------ resumen
    def _procesar_resumen(self, indice, grilla, pagina):
        for r, fila in enumerate(grilla):
            k = clave(fila[0])
            if not k and not any(fila[1:]):
                continue
            if self.cat.encabezados.get(k) == "periodo_fiscal":
                self.anios_resumen = [anio_de(v) for v in fila[1:]]
                continue
            id_resumen = self.cat.indice_resumen.get(k)
            if not id_resumen:
                if any(fila[1:]):
                    self.no_reconocidas.append({"etiqueta": fila[0], "contexto": "resumen_financiero",
                                                "pagina": pagina, "tabla": indice, "fila": r, "valores": fila[1:]})
                continue
            unidad = self.cat.resumen[id_resumen]["unidad"]
            valores = OrderedDict()
            for anio, texto in zip(self.anios_resumen, fila[1:]):
                if anio is not None:
                    valores[str(anio)] = self._convertir(texto, unidad, fila[0])
            self.resumen[id_resumen] = {"id": id_resumen, "etiqueta": fila[0], "unidad": unidad, "valores": valores,
                                        "fuente": {"pagina": pagina, "tabla": indice, "fila": r}}

    # -------------------------------------------------------------- pares
    def _procesar_pares(self, indice, grilla, pagina, continua):
        for r, fila in enumerate(grilla):
            if self.cat.encabezados.get(clave(fila[0])) == "compania" or not any(fila):
                continue
            self.filas_pares.append({"nombre": fila[0], "valores": self._alinear_pares(fila[1:]),
                                     "fuente": {"pagina": pagina, "tabla": indice, "fila": r}})
        if continua:
            self.correcciones.append({"tipo": "tabla_continuada", "seccion": "empresas_similares", "tabla": indice,
                                      "detalle": "Continuación de la tabla anterior en otra página; columnas alineadas desde la derecha."})

    def _alinear_pares(self, valores):
        total = len(self.cat.columnas_pares) - 1
        if len(valores) >= total:
            return list(valores[:total - 1]) + [valores[-1]]
        alineados = [""] * total
        for i, v in enumerate(valores[:-1]):
            alineados[i] = v
        alineados[-1] = valores[-1] if valores else ""
        return alineados

    def _cerrar_pares(self):
        filas = self.filas_pares
        fusionadas, i = [], 0
        while i < len(filas):
            actual = filas[i]
            llenas = [j for j, v in enumerate(actual["valores"]) if v]
            siguiente = filas[i + 1] if i + 1 < len(filas) else None
            if siguiente and len(llenas) < 3:
                llenas_sig = {j for j, v in enumerate(siguiente["valores"]) if v}
                if not set(llenas) & llenas_sig:
                    combinada = [a or b for a, b in zip(actual["valores"], siguiente["valores"])]
                    nombre = limpiar(f"{actual['nombre']} {siguiente['nombre']}")
                    self.correcciones.append({"tipo": "fila_partida_unida", "seccion": "empresas_similares",
                                              "empresa": nombre, "filas": [actual["fuente"], siguiente["fuente"]]})
                    fusionadas.append({"nombre": nombre, "valores": combinada, "fuente": actual["fuente"]})
                    i += 2
                    continue
            fusionadas.append(actual)
            i += 1

        for f in fusionadas:
            empresa = OrderedDict([("nombre", f["nombre"])])
            for col, texto in zip(self.cat.columnas_pares[1:], f["valores"]):
                empresa[col["id"]] = self._convertir(texto, col["unidad"], f["nombre"])
            empresa["fuente"] = f["fuente"]
            self.pares.append(empresa)

    # -------------------------------------------------------------- series
    def _procesar_serie(self, indice, grilla, pagina):
        ncols = len(grilla[0]) if grilla else 0
        tiene_encabezado = any(self.cat.encabezados.get(clave(f[0])) == "columna_nombre" for f in grilla)
        if not tiene_encabezado and self.anios_cols:
            self.correcciones.append({"tipo": "tabla_continuada", "tabla": indice, "pagina": pagina,
                                      "detalle": "Tabla sin fila de años; hereda los años de la tabla anterior."})
            if ncols != self.ncols_encabezado:
                self._realinear_anios(ncols)

        for r, fila in enumerate(grilla):
            self._procesar_fila_serie(indice, pagina, r, fila, ncols)

    def _realinear_anios(self, ncols):
        anios = [self.anios_cols[c] for c in sorted(self.anios_cols)]
        self.anios_cols = {ncols - len(anios) + i: a for i, a in enumerate(anios)}
        self.ncols_encabezado = ncols
        self._advertir("columnas_realineadas", "Una tabla continuada tiene otro número de columnas; los años se alinearon desde la derecha.")

    def _procesar_fila_serie(self, indice, pagina, r, fila, ncols):
        etiqueta = fila[0]
        k = clave(etiqueta)
        resto = fila[1:]
        if not k and not any(resto):
            return
        self.etiquetas_leidas.append(etiqueta)

        encabezado = self.cat.encabezados.get(k)
        if encabezado == "columna_nombre":
            self.anios_cols = {c: anio_de(v) for c, v in enumerate(fila) if c > 0 and anio_de(v)}
            self.anios_series.update(self.anios_cols.values())
            self.ncols_encabezado = ncols
            return
        if encabezado == "nota_ratios":
            self.mayor, self.bloque = "ratios", 3
            self.nota_ratios = etiqueta
            return
        if encabezado == "tasa_de_cambio":
            self.tasa_pendiente = self._valores_por_anio(fila, "numero", etiqueta)
            return

        if k in self.cat.titulos and not any(resto):
            self._aplicar_titulo(self.cat.titulos[k])
            self.ultima_fila_titulo = self.cat.titulos[k]
            return

        if k in self.cat.indice_detalles and k not in self.cat.indice_cuentas:
            self._guardar_detalle(self.cat.indice_detalles[k], fila, etiqueta)
            return

        candidatos = self.cat.indice_cuentas.get(k)
        if candidatos:
            cuenta = self._elegir_cuenta(candidatos, etiqueta)
            self._guardar_cuenta(cuenta, fila, etiqueta, {"pagina": pagina, "tabla": indice, "fila": r})
            return

        if any(es_numerico(v) for v in resto):
            self.no_reconocidas.append({"etiqueta": etiqueta, "contexto": self.seccion_actual, "pagina": pagina,
                                        "tabla": indice, "fila": r, "valores": resto})
        elif not any(resto):
            self._advertir("titulo_desconocido", f"Fila sin valores que no está en el catálogo: '{etiqueta}'.", pagina=pagina)

    def _aplicar_titulo(self, seccion):
        if seccion in SECCIONES_ESTADOS:
            self.seccion_actual = seccion
            self._resolver_tasa(seccion)
        elif seccion == "ratios" or seccion.startswith("ratios."):
            self.seccion_actual = "ratios"
            self.mayor = "ratios"
            self.bloque = max(self.bloque, 2)
            self._resolver_tasa("ratios")
        elif seccion == "otra_informacion":
            self.bloque = 3 if self.mayor == "ratios" else max(self.bloque, 1)
        elif seccion == "estados_financieros.detalles_reporte":
            if self.bloque >= 1 or any(self.series[s] for s in SECCIONES_ESTADOS):
                self.mayor, self.bloque = "ratios", max(self.bloque, 2)

    def _resolver_tasa(self, seccion):
        if self.tasa_pendiente is None:
            return
        campo = TASA_POR_SECCION[seccion]
        for anio, valor in self.tasa_pendiente.items():
            self.tasas.setdefault(anio, {})[campo] = valor
        self.tasa_pendiente = None

    def _elegir_cuenta(self, candidatos, etiqueta):
        if len(candidatos) == 1:
            return self.cat.cuentas[candidatos[0]]
        cuentas = [self.cat.cuentas[i] for i in candidatos]
        posicion = {id_: i for i, id_ in enumerate(self.vistos)}
        por_padre = sorted(cuentas, key=lambda c: posicion.get(c["padre"], -1), reverse=True)
        if posicion.get(por_padre[0]["padre"], -1) >= 0:
            return por_padre[0]
        en_seccion = [c for c in cuentas if c["seccion"] == self.seccion_actual]
        elegida = (en_seccion or cuentas)[0]
        self._advertir("etiqueta_ambigua", f"'{etiqueta}' coincide con varias cuentas; se eligió {elegida['id']}.")
        return elegida

    def _guardar_cuenta(self, cuenta, fila, etiqueta, fuente):
        seccion = cuenta["seccion"]
        if seccion == "ratios":
            self.mayor = "ratios"
            self.bloque = max(self.bloque, 2)
        self.seccion_actual = seccion
        self._resolver_tasa(seccion)
        valores = self._valores_por_anio(fila, cuenta["unidad"], etiqueta)
        destino = self.series[seccion]
        if cuenta["id"] in destino:
            existentes = destino[cuenta["id"]]["valores"]
            for anio, valor in valores.items():
                if existentes.get(anio) is None:
                    existentes[anio] = valor
                elif valor is not None and abs(existentes[anio] - valor) > 1e-9:
                    self._advertir("valor_duplicado_distinto", f"{cuenta['id']} aparece dos veces con valores distintos en {anio}.")
            return
        registro = OrderedDict([("id", cuenta["id"]), ("etiqueta", etiqueta)])
        if cuenta.get("grupo"):
            registro["grupo"] = cuenta["grupo"]
        registro.update([("padre", cuenta["padre"]), ("nivel", cuenta["nivel"]), ("unidad", cuenta["unidad"]),
                         ("valores", valores), ("fuente", fuente)])
        destino[cuenta["id"]] = registro
        self.vistos.append(cuenta["id"])

    def _guardar_detalle(self, campo, fila, etiqueta):
        i = self.bloque
        while i < len(BLOQUES):
            tipo = BLOQUES[i].split(".")[1]
            if campo in ACEPTA[tipo] and campo not in self.bloques[BLOQUES[i]]:
                break
            i += 1
        if i == len(BLOQUES):
            self._advertir("detalle_sin_bloque", f"No se pudo ubicar la fila de texto '{etiqueta}'.")
            return
        self.bloque = i
        if BLOQUES[i].startswith("ratios"):
            self.mayor = "ratios"
        valores = OrderedDict()
        for c, anio in sorted(self.anios_cols.items()):
            texto = fila[c] if c < len(fila) else ""
            valores[str(anio)] = self._texto_detalle(campo, texto)
        self.bloques[BLOQUES[i]][campo] = {"etiqueta": etiqueta, "valores": valores}

    def _texto_detalle(self, campo, texto):
        if self.cat.es_nulo(texto):
            return None
        if campo in CAMPOS_FECHA:
            return parsear_fecha(texto, self.cat.meses)
        return self.cat.valores_texto.get(clave(texto), limpiar(texto))

    # ------------------------------------------------------------- valores
    def _valores_por_anio(self, fila, unidad, etiqueta):
        valores = OrderedDict()
        for c, anio in sorted(self.anios_cols.items()):
            texto = fila[c] if c < len(fila) else ""
            valores[str(anio)] = self._convertir(texto, unidad, etiqueta)
        return valores

    def _convertir(self, texto, unidad, etiqueta):
        if self.cat.es_nulo(texto):
            return None
        if unidad in ("texto",):
            return self.cat.valores_texto.get(clave(texto), limpiar(texto))
        if unidad == "fecha":
            return parsear_fecha(texto, self.cat.meses)
        if unidad == "anio":
            return anio_de(texto)
        numero = parsear_numero(texto)
        if numero is None:
            self._advertir("valor_no_numerico", f"'{texto}' no es un número válido en '{etiqueta}'.")
            return None
        valor, sufijo = numero
        if unidad in ("fraccion", "fraccion_sin_simbolo") or sufijo == "%":
            valor = valor / 100
        return redondear(valor)

    # ------------------------------------------------------------ párrafos
    def _leer_bloques_resumen(self, textos):
        """Bloques de la hoja resumen que el EMIS en español entrega como párrafos sueltos y no como tabla:
        'Hoja Resumen de Compañía - Principales Ejecutivos' (nombre, cargo, nombre, cargo...),
        '... - Detalles de Accionistas' y '... - Subsidiarias y filiales' (un nombre por párrafo, a veces con
        el sufijo societario partido en el párrafo siguiente). Devuelve los índices que consumió."""
        usados = set()
        for i, texto in enumerate(textos):
            k = clave(texto)
            if not any(m in k for m in MARCAS_HOJA_RESUMEN):
                continue
            inicio = i + 1
            if k in MARCAS_HOJA_RESUMEN and texto.rstrip().endswith("-") and i + 1 < len(textos):
                # Título partido: 'Company Tear Sheet -' y en el párrafo siguiente 'Key Executives'
                k += clave(textos[i + 1])
                inicio = i + 2
            tipo = next((t for t, palabras in BLOQUES_RESUMEN.items() if any(p in k for p in palabras)), None)
            if tipo is None:
                continue
            items, j = [], inicio
            while j < len(textos) and len(items) < 40:
                siguiente = textos[j]
                ks = clave(siguiente)
                if not siguiente:
                    j += 1
                    continue
                encabezado_mayusculas = siguiente.isupper() and " " in siguiente  # 'PERÍODO FISCAL'
                if any(m in ks for m in MARCAS_HOJA_RESUMEN) or ks in self.cat.titulos or encabezado_mayusculas:
                    break
                items.append(siguiente)
                j += 1
            if not items:
                continue
            usados.update(range(i, j))
            if tipo == "ejecutivos":
                pares = [OrderedDict([("nombre", items[n]), ("cargo", items[n + 1])])
                         for n in range(0, len(items) - 1, 2)]
                self.textos["ejecutivos_clave"] = pares
                revisores = [p["nombre"] for p in pares if "revisorfiscal" in clave(p["cargo"])
                             or "auditor" in clave(p["cargo"])]
                if revisores:
                    self.textos.setdefault("informacion_basica", OrderedDict())["revisor_fiscal"] = revisores
            else:
                nombres = []
                for item in items:
                    if nombres and RE_SUFIJO_SOCIETARIO.match(item):
                        nombres[-1] = f"{nombres[-1]} {item}"
                    else:
                        nombres.append(item)
                self.textos.setdefault("informacion_basica", OrderedDict())[tipo] = nombres
        return usados

    def _leer_moneda_original(self, textos):
        """'Unidades originales como se reportó' seguida de 'COP Miles' en párrafos (EMIS en español)."""
        etiquetas = {clave(e) for d in [self.cat.detalles.get("original_units") or {}]
                     for es in (d.get("etiquetas") or {}).values() for e in es}
        for i, texto in enumerate(textos[:-1]):
            if clave(texto) in etiquetas:
                ks = clave(textos[i + 1])
                for original, normalizado in self.cat.valores_texto.items():
                    if original and ks.startswith(original):
                        self.moneda_original_parrafo = normalizado
                        return

    def _procesar_parrafos(self, parrafos):
        if not parrafos:
            return
        textos = [limpiar(p.get("content", "") if isinstance(p, dict) else p) for p in parrafos]
        textos = [t for t in textos if not RE_RUIDO_PAGINA.match(t)]  # pies de página del PDF
        usados = self._leer_bloques_resumen(textos)
        self._leer_moneda_original(textos)
        pendiente = None
        for n, texto in enumerate(textos):
            if not texto or n in usados:
                continue
            pares = self._pares_empresa(texto)
            if len(pares) >= 2 or (pares and pares[0][0] == "sede"):
                for campo_id, valor in pares:
                    if valor and not self.empresa.get(campo_id):
                        self._guardar_campo_empresa(campo_id, valor)
                pendiente = None
                continue
            if pendiente:
                if clave(texto) in self.cat.indice_texto:  # etiqueta vacía seguida de otra etiqueta
                    pendiente = self.cat.indice_texto[clave(texto)]
                    continue
                seccion, campo = pendiente
                if campo not in self.textos.get(seccion, {}):  # lo que vino de una tabla manda
                    self.textos.setdefault(seccion, OrderedDict())[campo] = texto
                pendiente = None
                continue
            etiqueta, _, valor = texto.partition(":")
            k = clave(etiqueta)
            if k in self.cat.indice_texto:
                seccion, campo = self.cat.indice_texto[k]
                if valor.strip():
                    self.textos.setdefault(seccion, OrderedDict())[campo] = limpiar(valor)
                else:
                    pendiente = (seccion, campo)
            elif k in self.cat.indice_empresa and valor.strip():
                campo = self.cat.indice_empresa[k]
                if not self.empresa.get(campo):
                    self._guardar_campo_empresa(campo, valor)
            elif clave(texto) in self.cat.indice_texto:
                pendiente = self.cat.indice_texto[clave(texto)]
            elif clave(texto) not in self.cat.titulos:
                self.parrafos_sin_clasificar.append(texto)

    # -------------------------------------------------------------- salida
    def _limpiar_ceros_flujo(self):
        """EMIS llena con 0.00 el detalle del flujo de caja de los años en que solo tiene el total
        (Rentek 2021: compra de PPE 0.00 con flujo de inversión de -11.123; depreciación y cambios de
        capital de trabajo 0.00 con flujo operativo de 2.968 y solo la utilidad neta informada).

        Dos reglas, en un año dado, bajo un total distinto de cero:
          1. todas las líneas hijas valen 0.00: no hay detalle, pasan a null;
          2. una línea hija es un subtotal (tiene líneas propias) y él y todo lo que cuelga de él valen 0.00:
             es un bloque vacío (Rentek 2021: efectivo generado por la operación y sus ajustes), pasa a null.
        Un 0.00 suelto, con detalle alrededor, se respeta: puede ser real."""
        flujo = self.series["estados_financieros.flujo_de_caja"]
        hijos = {}
        for id_, fila in flujo.items():
            if fila.get("padre") in flujo:
                hijos.setdefault(fila["padre"], []).append(id_)

        def subarbol(id_):
            yield id_
            for h in hijos.get(id_, []):
                yield from subarbol(h)

        def anular(ids, anio):
            for i in ids:
                for j in subarbol(i):
                    if flujo[j]["valores"].get(anio) == 0:
                        flujo[j]["valores"][anio] = None

        for padre, ids in hijos.items():
            for anio, total in list(flujo[padre]["valores"].items()):
                if not total:
                    continue
                valores = [flujo[i]["valores"].get(anio) for i in ids]
                if all(v == 0 for v in valores):
                    vacias = ids
                else:
                    vacias = [i for i in ids if hijos.get(i)
                              and all(flujo[j]["valores"].get(anio) in (0, None) for j in subarbol(i))
                              and flujo[i]["valores"].get(anio) == 0]
                if not vacias:
                    continue
                anular(vacias, anio)
                self.correcciones.append({"tipo": "detalle_flujo_sin_dato", "seccion": "flujo_de_caja",
                                          "total": padre, "anio": anio, "lineas": vacias,
                                          "detalle": "Bloque de detalle en 0.00 con el total distinto de cero; "
                                                     "se dejó en null."})

    def _armar_salida(self, entrada, n_tablas):
        self._limpiar_ceros_flujo()
        if self.ejecutivos_tabla and not self.textos.get("ejecutivos_clave"):
            self.textos["ejecutivos_clave"] = self.ejecutivos_tabla
            revisores = [e["nombre"] for e in self.ejecutivos_tabla
                         if "revisorfiscal" in clave(e["cargo"]) or "auditor" in clave(e["cargo"])]
            if revisores:
                self.textos.setdefault("informacion_basica", OrderedDict()).setdefault("revisor_fiscal", revisores)
        idioma = self.cat.detectar_idioma(self.etiquetas_leidas + [r["etiqueta"] for r in self.resumen.values()])
        anios = sorted(self.anios_series)
        faltantes_anios = [a for a in range(anios[0], anios[-1] + 1) if a not in anios] if anios else []

        estados = OrderedDict()
        estados["detalles_reporte"] = self.bloques["estados.detalles"]
        estados["periodos"] = self._periodos(anios)
        for s in SECCIONES_ESTADOS:
            estados[s.split(".")[1]] = list(self.series[s].values())
        estados["otra_informacion"] = self.bloques["estados.otra"]

        ratios = OrderedDict()
        ratios["detalles_reporte"] = self.bloques["ratios.detalles"]
        for g in GRUPOS_RATIOS:
            ratios[g] = [f for f in self.series["ratios"].values() if f.get("grupo") == g]
        otra_ratios = OrderedDict(self.bloques["ratios.otra"])
        if self.nota_ratios:
            otra_ratios["nota"] = self.nota_ratios
        ratios["otra_informacion"] = otra_ratios

        valores = {}
        for filas in self.series.values():
            for id_, f in filas.items():
                valores[id_] = f["valores"]
        validaciones = validar(self.cat, valores, self.tasas, self.resumen)

        faltantes, ausentes = self._faltantes()
        sin_parrafos = not entrada.get("parrafos")
        secciones_texto = ["clasificacion_naics", "descripcion_negocio", "informacion_basica", "ejecutivos_clave"]
        no_disponibles = [s for s in secciones_texto if s not in self.textos]
        for s in ausentes:
            self._advertir("seccion_ausente", f"El reporte no trae la sección {s}.")
        if faltantes_anios:
            self._advertir("anios_faltantes", f"La serie no trae los años {faltantes_anios}.")
        if self.no_reconocidas:
            self._advertir("filas_no_reconocidas", f"{len(self.no_reconocidas)} filas con valores no están en el catálogo.")
        for v in validaciones:
            if v["severidad"] == "advertencia" and v["fallas"]:
                self._advertir("validacion", f"No cuadra: {v['descripcion']} en {v['fallas']}.")

        clave_faltantes = self._cuentas_clave_faltantes()
        if clave_faltantes:
            self._advertir("cuentas_clave_faltantes",
                           f"El reporte no dejó leer cuentas que usan los KPIs: {clave_faltantes}. "
                           "Revisar filas_no_reconocidas y ampliar el catálogo.")
        no_reconocidas_estados = [r for r in self.no_reconocidas
                                  if str(r.get("contexto") or "").startswith(("estados_financieros", "ratios"))]

        if any(v["severidad"] == "error" and v["fallas"] for v in validaciones):
            estado = "error"
        elif self.advertencias:
            estado = "con_advertencias"
        else:
            estado = "ok"

        salida = OrderedDict()
        salida["metadata"] = OrderedDict([
            ("fuente", "EMIS"),
            ("archivo_origen", entrada.get("archivo_origen")),
            ("fecha_extraccion", entrada.get("fecha_extraccion")),
            ("modelo", entrada.get("modelo")),
            ("fecha_estandarizacion", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
            ("idioma_reporte", idioma),
            ("unidades", "usd_miles"),
            ("convencion_porcentajes", "fraccion (20.05% = 0.2005)"),
            ("version_esquema", VERSION_ESQUEMA),
            ("version_catalogo", self.cat.version),
            ("tablas_recibidas", n_tablas),
        ])
        salida["empresa"] = self.empresa or None
        for s in secciones_texto:
            salida[s] = self.textos.get(s)
        salida["resumen_financiero"] = OrderedDict([("anios", [a for a in self.anios_resumen if a]),
                                                    ("filas", list(self.resumen.values()))])
        salida["empresas_similares"] = self.pares
        salida["estados_financieros"] = estados
        salida["ratios"] = ratios
        salida["filas_no_reconocidas"] = self.no_reconocidas
        salida["calidad"] = OrderedDict([
            ("estado", estado),
            ("anios", anios),
            ("anios_faltantes", faltantes_anios),
            ("secciones_ausentes", ausentes),
            ("secciones_no_disponibles", [{"seccion": s, "motivo": "La entrada no trae 'parrafos'." if sin_parrafos
                                           else "Los párrafos no traen esta sección."} for s in no_disponibles]),
            ("cuentas_faltantes", faltantes),
            ("cuentas_clave_faltantes", clave_faltantes),
            ("filas_estados_no_reconocidas", len(no_reconocidas_estados)),
            ("correcciones", self.correcciones),
            ("validaciones", validaciones),
            ("advertencias", self.advertencias),
        ])
        salida["cifras_compactas"] = OrderedDict([
            ("unidades", "usd_miles"),
            ("convencion_porcentajes", "fraccion"),
            ("anios", anios),
            ("anios_faltantes", faltantes_anios),
            ("estados", OrderedDict((id_, f["valores"]) for s in SECCIONES_ESTADOS
                                    for id_, f in self.series[s].items())),
            ("ratios", OrderedDict((id_, f["valores"]) for id_, f in self.series["ratios"].items())),
            # Sección de cada cuenta: la Function 3 la usa para saber qué tasa de cambio aplicó EMIS.
            ("secciones", OrderedDict((id_, SECCION_CORTA[s]) for s in SECCIONES_ESTADOS
                                      for id_ in self.series[s])),
            # Tasas de cambio de EMIS (moneda original por USD) por año.
            ("tasas", self._tasas_compactas(anios)),
            ("moneda_original", self._moneda_original()),
            ("empleados", self._empleados()),
            ("calidad", OrderedDict([("estado", estado),
                                     ("fallas", {v["id"]: v["fallas"] for v in validaciones if v["fallas"]}),
                                     ("cuentas_clave_faltantes", clave_faltantes),
                                     ("filas_estados_no_reconocidas", len(no_reconocidas_estados))])),
        ])
        if self.parrafos_sin_clasificar:
            salida["calidad"]["parrafos_sin_clasificar"] = self.parrafos_sin_clasificar
        return salida

    def _periodos(self, anios):
        detalles = self.bloques["estados.detalles"]
        otra = self.bloques["estados.otra"]
        periodos = []
        for anio in anios:
            a = str(anio)
            fin = (detalles.get("period_end_date") or otra.get("period_end_date") or {}).get("valores", {}).get(a)
            inicio = (otra.get("begin_date") or {}).get("valores", {}).get(a)
            tasas = self.tasas.get(a, {})
            periodos.append(OrderedDict([("anio", anio), ("inicio", inicio), ("fin", fin),
                                         ("tasa_resultados", tasas.get("tasa_resultados")),
                                         ("tasa_balance", tasas.get("tasa_balance")),
                                         ("tasa_flujo", tasas.get("tasa_flujo")),
                                         ("tasa_ratios", tasas.get("tasa_ratios"))]))
        return periodos

    def _tasas_compactas(self, anios):
        """{"2024": {"resultados": 4071.35, "balance": 4409.15, "flujo": 4071.35}}. Si EMIS no muestra
        la tasa del flujo, usa la de resultados (EMIS convierte el flujo con la tasa promedio)."""
        salida = OrderedDict()
        for anio in anios:
            t = self.tasas.get(str(anio), {})
            resultados = t.get("tasa_resultados")
            salida[str(anio)] = OrderedDict([
                ("resultados", resultados),
                ("balance", t.get("tasa_balance")),
                ("flujo", t.get("tasa_flujo") or resultados),
            ])
        return salida

    def _moneda_original(self):
        """Moneda en que la empresa reporta, si EMIS la muestra ('original_units')."""
        for bloque in ("estados.otra", "ratios.otra"):
            campo = self.bloques[bloque].get("original_units")
            if campo:
                for valor in campo["valores"].values():
                    if valor:
                        return valor
        return self.moneda_original_parrafo

    def _empleados(self):
        """'65 (2026)' -> {"valor": 65, "anio": 2026, "texto": "65 (2026)"}."""
        texto = (self.textos.get("informacion_basica") or {}).get("total_empleados")
        if not texto:
            return None
        m = _RE_EMPLEADOS.search(texto)
        if not m:
            return OrderedDict([("valor", None), ("anio", None), ("texto", texto)])
        numero = parsear_numero(m.group(1))
        return OrderedDict([("valor", numero[0] if numero else None),
                            ("anio", int(m.group(2)) if m.group(2) else None),
                            ("texto", texto)])

    def _cuentas_clave_faltantes(self):
        presentes = set()
        for s in SECCIONES_ESTADOS:
            for id_, f in self.series[s].items():
                if any(v is not None for v in f["valores"].values()):
                    presentes.add(id_)
        return [grupo for grupo, ids in CUENTAS_CLAVE if not presentes.intersection(ids)]

    def _faltantes(self):
        faltantes, ausentes = OrderedDict(), []
        for s in SECCIONES_ESTADOS:
            nombre = s.split(".")[1]
            esperados = self.cat.ids_de_seccion(s)
            if not self.series[s]:
                ausentes.append(nombre)
            faltantes[nombre] = [i for i in esperados if i not in self.series[s]]
        for g in GRUPOS_RATIOS:
            esperados = self.cat.ids_de_seccion("ratios", g)
            presentes = [i for i in esperados if i in self.series["ratios"]]
            if not presentes:
                ausentes.append(f"ratios.{g}")
            faltantes[f"ratios.{g}"] = [i for i in esperados if i not in presentes]
        faltantes["resumen_financiero"] = [r for r in self.cat.resumen if r not in self.resumen]
        if not self.empresa:
            ausentes.append("empresa")
        if not self.pares:
            ausentes.append("empresas_similares")
        return faltantes, ausentes


def estandarizar(entrada, catalogo=None):
    """Punto de entrada simple: recibe el JSON de Document Intelligence y devuelve el JSON estandarizado."""
    return Estandarizador(catalogo).procesar(entrada)
