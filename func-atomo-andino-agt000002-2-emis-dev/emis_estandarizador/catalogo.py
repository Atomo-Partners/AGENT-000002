"""Carga el catálogo de la plantilla EMIS y arma los índices por etiqueta normalizada."""

import json
import os

from .utilidades import clave

RUTA_POR_DEFECTO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "catalogo", "catalogo_emis.json")


class Catalogo:
    def __init__(self, datos):
        self.datos = datos
        self.version = datos.get("version")
        self.cuentas = {c["id"]: c for c in datos["cuentas"]}
        self.meses = datos.get("meses", {})
        self.nulos = {clave(n) for n in datos["valores_de_texto"]["nulos"]}
        self.tolerancias = datos["validaciones"]
        self.claves_por_idioma = {"en": set(), "es": set()}

        self.indice_cuentas = {}
        for c in datos["cuentas"]:
            for idioma, etiquetas in c["etiquetas"].items():
                for e in etiquetas:
                    k = clave(e)
                    candidatos = self.indice_cuentas.setdefault(k, [])
                    if c["id"] not in candidatos:  # misma etiqueta en dos idiomas no es ambigüedad
                        candidatos.append(c["id"])
                    self.claves_por_idioma[idioma].add(k)

        self.indice_resumen = self._indice(datos["resumen_financiero"])
        self.resumen = {r["id"]: r for r in datos["resumen_financiero"]}
        self.indice_detalles = self._indice(datos["detalles_y_otra_informacion"])
        self.detalles = {d["id"]: d for d in datos["detalles_y_otra_informacion"]}
        self.indice_empresa = self._indice(datos["empresa"])
        self.empresa = {e["id"]: e for e in datos["empresa"]}

        self.indice_texto = {}
        for seccion, campos in datos["secciones_de_texto"].items():
            if isinstance(campos, list):
                for campo in campos:
                    for etiquetas in campo["etiquetas"].values():
                        for e in etiquetas:
                            self.indice_texto[clave(e)] = (seccion, campo["id"])

        self.titulos = {}
        for seccion, por_idioma in datos["titulos_de_seccion"].items():
            for idioma, etiquetas in por_idioma.items():
                for e in etiquetas:
                    self.titulos[clave(e)] = seccion
                    self.claves_por_idioma[idioma].add(clave(e))

        self.encabezados = {}
        for nombre, por_idioma in datos["encabezados"].items():
            for idioma, etiquetas in por_idioma.items():
                for e in etiquetas:
                    self.encabezados[clave(e)] = nombre
                    self.claves_por_idioma[idioma].add(clave(e))

        self.valores_texto = {}
        for grupo, mapa in datos["valores_de_texto"].items():
            if isinstance(mapa, dict):
                for original, normalizado in mapa.items():
                    self.valores_texto[clave(original)] = normalizado

        self.columnas_pares = datos["empresas_similares"]["columnas"]

    @staticmethod
    def _indice(entradas):
        indice = {}
        for entrada in entradas:
            for etiquetas in entrada["etiquetas"].values():
                for e in etiquetas:
                    indice[clave(e)] = entrada["id"]
        return indice

    @classmethod
    def cargar(cls, ruta=None):
        with open(ruta or RUTA_POR_DEFECTO, encoding="utf-8") as f:
            return cls(json.load(f))

    def es_nulo(self, texto):
        return clave(texto) in self.nulos

    def detectar_idioma(self, etiquetas):
        """Cuenta cuántas etiquetas solo existen en inglés o solo en español."""
        solo_en = self.claves_por_idioma["en"] - self.claves_por_idioma["es"]
        solo_es = self.claves_por_idioma["es"] - self.claves_por_idioma["en"]
        en = sum(1 for e in etiquetas if clave(e) in solo_en)
        es = sum(1 for e in etiquetas if clave(e) in solo_es)
        if en == es == 0:
            return "desconocido"
        return "en" if en >= es else "es"

    def ids_de_seccion(self, seccion, grupo=None):
        return [c["id"] for c in self.datos["cuentas"]
                if c["seccion"] == seccion and (grupo is None or c.get("grupo") == grupo)]
