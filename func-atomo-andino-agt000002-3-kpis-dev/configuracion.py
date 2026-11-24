"""Configuración fija de la Function 3, generada con la skill atomo-config-fase2.

config/catalogo_kpis.json     Foto del catálogo kn__indicadores_emis (con correcciones locales).
config/scorecard_cortes.json  Rangos de kn__scorecard_indicadores ya traducidos a cortes numéricos.
config/formulas_kpi.json      Fórmula de cada indicador del catálogo en la sintaxis del motor.
config/ipc.json               IPC por país (Colombia, DANE) para deflactar el crecimiento en USD.
config/industrias.json        Sector priorizado -> industria del catálogo y parámetros fijos.

Se carga una vez por instancia. Para cambiarla se vuelve a correr la skill y se publica la Function.
"""
import json
import os
import re
import unicodedata
from functools import lru_cache

CARPETA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")


def _leer(nombre):
    with open(os.path.join(CARPETA, nombre), encoding="utf-8") as f:
        return json.load(f)


def normalizar(texto):
    """Sin tildes, minúsculas y solo letras y dígitos: 'Construcción liviana' -> 'construccionliviana'."""
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", t)


class Configuracion:
    def __init__(self):
        self.catalogo = _leer("catalogo_kpis.json")
        self.cortes = _leer("scorecard_cortes.json")
        self.formulas = _leer("formulas_kpi.json")
        self.ipc = _leer("ipc.json")
        self.industrias_cfg = _leer("industrias.json")

        self.indicadores = {i["indicador_id"]: i for i in self.catalogo["indicadores"]}
        self.industrias = sorted(self.cortes["industrias"])
        self._industria_por_clave = {normalizar(i): i for i in self.industrias}
        self._sector_por_clave = {normalizar(s): i for s, i in self.industrias_cfg["sector_a_industria"].items()}
        self._pais_por_clave = {normalizar(p): p for p in self.ipc["paises"]}
        self.umbral_cambiario = float(self.industrias_cfg.get("umbral_movimiento_cambiario_pct", 10))
        self.eliminatorios = set(self.industrias_cfg.get("eliminatorios") or [])
        self.vigencia_empleados = int(self.industrias_cfg.get("anios_vigencia_empleados", 1))

    # ---------------------------------------------------------------- industrias
    def industria_valida(self, nombre):
        """Devuelve el nombre exacto del catálogo, o None. Tolera tildes y mayúsculas."""
        return self._industria_por_clave.get(normalizar(nombre))

    def industria_de_sector(self, sector):
        return self._sector_por_clave.get(normalizar(sector)) or self.industria_valida(sector)

    def metrica_ancla(self, industria):
        datos = self.cortes["industrias"].get(industria) or {}
        return datos.get("metrica_ancla")

    def anclas_por_industria(self):
        return [{"industria": i, "metrica_ancla": self.metrica_ancla(i)} for i in self.industrias]

    def filas_scorecard(self, industria):
        return (self.cortes["industrias"].get(industria) or {}).get("filas") or {}

    # ---------------------------------------------------------------- indicadores
    def kpis_en_alcance(self, industria):
        """Generales (todas las empresas) más los propios de la industria, en el orden del catálogo."""
        return [i for i in self.catalogo["indicadores"]
                if i["alcance"] == "general" or (industria and i.get("industria") == industria)]

    def catalogo_para_agentes(self):
        """El catálogo con la forma que ya recibía el selector (la misma del Select Catalogo_KPIs)."""
        campos = ("indicador_id", "nombre", "alcance", "industria", "eje", "unidad", "fuente_dato", "formula",
                  "por_que", "serie")
        return [{c: i.get(c) for c in campos} for i in self.catalogo["indicadores"]]

    def expresion_ancla(self, industria):
        ancla = self.metrica_ancla(industria) or "EBITDA"
        return ancla, self.formulas["anclas"].get(ancla, "{EBITDA}")

    # ---------------------------------------------------------------- IPC y moneda
    def pais_ipc(self, pais):
        """Resuelve el país del deal contra config/ipc.json. Sin país usa el de por defecto.
        Devuelve (pais, motivo): pais None si el país llegó y no tiene IPC configurado."""
        if pais is None or not str(pais).strip():
            return self.ipc["pais_por_defecto"], "El país no llegó; se usa el país por defecto de config/ipc.json"
        encontrado = self._pais_por_clave.get(normalizar(pais))
        if encontrado:
            return encontrado, ""
        return None, f"No hay IPC configurado para '{pais}' en config/ipc.json"

    def ipc_de_pais(self, pais):
        """{anio: variación %} con la medida de config/ipc.json, y la ficha para reportar qué se usó."""
        resuelto, motivo = self.pais_ipc(pais)
        if resuelto is None:
            return {}, {"pais": None, "pais_recibido": pais, "motivo": motivo}
        datos = self.ipc["paises"][resuelto]
        medida = self.ipc["medida"]
        serie = {int(a): v[medida] for a, v in datos["por_anio"].items() if v.get(medida) is not None}
        ficha = {"pais": resuelto, "pais_recibido": pais, "moneda": datos.get("moneda"), "medida": medida,
                 "fuente": datos.get("fuente"), "estado": datos.get("estado"), "leido": datos.get("leido"),
                 "motivo": motivo}
        return serie, ficha

    def moneda_de_pais(self, pais):
        resuelto, _ = self.pais_ipc(pais)
        return (self.ipc["paises"].get(resuelto) or {}).get("moneda") if resuelto else None

    def ancla_desde_texto(self, texto):
        """Traduce la métrica ancla que escribe el plan ('EBIT', 'Utilidad antes de impuestos (UAI)', ...) a una
        de config/formulas_kpi.json. Devuelve (nombre, expresión) o (None, None) si no se reconoce."""
        k = normalizar(texto)
        if not k:
            return None, None
        if "ebitda" in k:
            nombre = "EBITDA"
        elif "ebit" in k:
            nombre = "EBIT"
        elif "antesdeimpuesto" in k or k in ("uai", "pbt", "ebt"):
            nombre = "Utilidad antes de impuestos"
        else:
            return None, None
        expresion = self.formulas["anclas"].get(nombre)
        return (nombre, expresion) if expresion else (None, None)

    def version(self):
        return {"catalogo": self.catalogo.get("version"), "cortes": self.cortes.get("version"),
                "formulas": self.formulas.get("version"), "formulas_revision": self.formulas.get("estado_revision"),
                "ipc": self.ipc.get("version")}


@lru_cache(maxsize=1)
def cargar():
    return Configuracion()
