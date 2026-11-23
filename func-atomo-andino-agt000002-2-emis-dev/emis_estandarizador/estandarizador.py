"""Estandariza la salida de Document Intelligence (prebuilt-layout) de un reporte EMIS
en un JSON organizado por secciones, guiado por el catálogo de la plantilla EMIS."""

from collections import OrderedDict
from datetime import datetime, timezone

from .catalogo import Catalogo
from .utilidades import anio_de, clave, es_numerico, limpiar, parsear_fecha, parsear_numero, redondear
from .validaciones import validar

VERSION_ESQUEMA = "1.0"

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

    # ------------------------------------------------------------ empresa
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
    def _procesar_parrafos(self, parrafos):
        if not parrafos:
            return
        textos = [limpiar(p.get("content", "") if isinstance(p, dict) else p) for p in parrafos]
        pendiente = None
        for texto in textos:
            if not texto:
                continue
            if pendiente:
                seccion, campo = pendiente
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
    def _armar_salida(self, entrada, n_tablas):
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
            ("calidad", OrderedDict([("estado", estado),
                                     ("fallas", {v["id"]: v["fallas"] for v in validaciones if v["fallas"]})])),
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
