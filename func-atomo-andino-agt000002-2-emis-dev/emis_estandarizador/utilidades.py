"""Utilidades de texto, números y fechas para leer reportes EMIS en inglés o español."""

import re
import unicodedata

_RE_NUMERO = re.compile(r"^\(?(-?)\s*([\d,]*\d(?:\.\d+)?)\)?\s*([%x×]?)$")
_RE_ANIO = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_RE_FECHA_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_RE_FECHA_EN = re.compile(r"^([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})$")
_RE_FECHA_ES = re.compile(r"^(\d{1,2})\s+de\s+([A-Za-zñÑáéíóú]+)\s+de\s+(\d{4})$", re.IGNORECASE)


def clave(texto):
    """Normaliza una etiqueta: sin tildes, en minúsculas y solo con letras y dígitos."""
    if texto is None:
        return ""
    sin_tildes = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", sin_tildes.lower())


def limpiar(texto):
    """Une saltos de línea y espacios repetidos."""
    if texto is None:
        return ""
    return re.sub(r"\s+", " ", str(texto)).strip()


def parsear_numero(texto):
    """Devuelve (valor, sufijo) o None. Sufijo es '%', 'x' o ''. Acepta '1,234.56', '-573', '20.05%', '1.41x'."""
    t = limpiar(texto).replace(" ", "")
    if not t:
        return None
    m = _RE_NUMERO.match(t)
    if not m:
        return None
    signo, cuerpo, sufijo = m.groups()
    # La coma solo es separador de miles si cada grupo tras ella tiene 3 dígitos.
    # "49,86%" o "1.083,42" (coma decimal) se rechazan en vez de leerse mal.
    entero = cuerpo.split(".")[0]
    if "," in entero and not re.fullmatch(r"\d{1,3}(,\d{3})+", entero):
        return None
    try:
        valor = float(cuerpo.replace(",", ""))
    except ValueError:
        return None
    if signo or (t.startswith("(") and t.endswith(")")):
        valor = -valor
    sufijo = "x" if sufijo in ("x", "×") else sufijo
    return valor, sufijo


def es_numerico(texto):
    return parsear_numero(texto) is not None


def anio_de(texto):
    """Extrae el año de '2021', 'Y-2021 INDIV.' o '2021Y Individual'."""
    m = _RE_ANIO.search(limpiar(texto))
    return int(m.group(1)) if m else None


def parsear_fecha(texto, meses):
    """Convierte '2020-12-31', 'April 22, 2008' o '25 de marzo de 2011' a 'AAAA-MM-DD'.
    Si no reconoce el formato, devuelve el texto original."""
    t = limpiar(texto)
    if not t:
        return None
    if _RE_FECHA_ISO.match(t):
        return t
    m = _RE_FECHA_EN.match(t)
    if m:
        mes = meses.get("en", {}).get(m.group(1).lower())
        if mes:
            return f"{int(m.group(3)):04d}-{mes:02d}-{int(m.group(2)):02d}"
    m = _RE_FECHA_ES.match(t)
    if m:
        mes = meses.get("es", {}).get(clave(m.group(2)))
        if mes:
            return f"{int(m.group(3)):04d}-{mes:02d}-{int(m.group(1)):02d}"
    return t


def redondear(valor, decimales=6):
    return None if valor is None else round(valor, decimales)
