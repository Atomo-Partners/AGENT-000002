"""
Motor de cálculo de KPIs de AGENT-000002 · Fase 2.

No decide fórmulas: evalúa, año por año, la formula_calculo que trae el agente
selector. Sintaxis aceptada:
    {clave}         valor del concepto `clave` en el año que se calcula
    {clave@t-1}     valor del concepto en el año anterior (también @t-2, etc.)
    {ipc}           IPC del año, en porcentaje (5.2 = 5,2 %)
    + - * / ( )     operadores
    números         constantes (100, 365, 1, 2...)
"""
import ast
import re
import statistics

VERSION_MOTOR = "2.0"
TOKEN = re.compile(r"\{\s*([A-Za-z0-9_\.\-áéíóúñÁÉÍÓÚÑ ]+?)\s*(?:@\s*t\s*-\s*(\d+))?\s*\}")
CONSTANTES_ESPERADAS = {0, 1, 2, 4, 12, 100, 360, 365}
OPERADORES = (ast.Add, ast.Sub, ast.Mult, ast.Div)


# ---------------------------------------------------------------------------
# 1. Normalizar cifras e IPC a {concepto: {anio: valor}}
# ---------------------------------------------------------------------------
def _a_numero(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(" ", "")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    if isinstance(v, dict):
        for k in ("valor", "value", "monto"):
            if k in v:
                return _a_numero(v[k])
    return None


def _es_anio(k):
    try:
        a = int(str(k)[:4])
        return 1900 <= a <= 2100
    except ValueError:
        return False


def _serie_desde_dict(d):
    """{"2021": 10, "2022": 12} -> {2021: 10.0, 2022: 12.0}"""
    serie = {}
    for k, v in d.items():
        if _es_anio(k):
            n = _a_numero(v)
            if n is not None:
                serie[int(str(k)[:4])] = n
    return serie


def normalizar_cifras(cifras):
    """
    Acepta las formas más comunes de cifras_emis.json:
      A) {"conceptos": {"ingresos": {"2021": 1, ...}, ...}}
      B) {"ingresos": {"2021": 1, ...}, ...}
      C) [{"concepto": "ingresos", "anio": 2021, "valor": 1}, ...]  (o dentro de "cifras"/"datos")
      D) {"anios": {"2021": {"ingresos": 1, ...}}}  o  {"2021": {"ingresos": 1}}
    Si tu estandarizador entrega otra forma, ajusta solo esta función.
    """
    if isinstance(cifras, str):
        import json
        cifras = json.loads(cifras)

    # Forma del estandarizador de EMIS (func-emis): vista compacta o salida completa.
    if isinstance(cifras, dict) and isinstance(cifras.get("cifras_compactas"), dict):
        cifras = cifras["cifras_compactas"]
    if isinstance(cifras, dict) and ("estados_financieros" in cifras or "ratios" in cifras or "estados" in cifras):
        return _desde_estandarizador(cifras)

    if isinstance(cifras, dict):
        for envoltura in ("cifras", "datos", "data", "conceptos"):
            if envoltura in cifras and isinstance(cifras[envoltura], (dict, list)):
                interno = normalizar_cifras(cifras[envoltura])
                if interno:
                    return interno

    resultado = {}
    if isinstance(cifras, list):  # forma C
        for fila in cifras:
            if not isinstance(fila, dict):
                continue
            c = fila.get("concepto") or fila.get("clave") or fila.get("codigo")
            a = fila.get("anio") or fila.get("año") or fila.get("year")
            n = _a_numero(fila.get("valor", fila.get("value")))
            if c and a is not None and _es_anio(a) and n is not None:
                resultado.setdefault(str(c), {})[int(str(a)[:4])] = n
        return resultado

    if isinstance(cifras, dict):
        anios = cifras.get("anios") if isinstance(cifras.get("anios"), dict) else None
        claves_anio = [k for k in cifras if _es_anio(k)]
        if anios or (claves_anio and len(claves_anio) == len(cifras)):  # forma D
            fuente = anios or cifras
            for a, conceptos in fuente.items():
                if _es_anio(a) and isinstance(conceptos, dict):
                    for c, v in conceptos.items():
                        n = _a_numero(v)
                        if n is not None:
                            resultado.setdefault(str(c), {})[int(str(a)[:4])] = n
            return resultado
        for c, v in cifras.items():  # forma B
            if isinstance(v, dict):
                serie = _serie_desde_dict(v)
                if serie:
                    resultado[str(c)] = serie
    return resultado


SECCIONES_ESTADOS = ("estado_resultados", "balance_general", "flujo_de_caja")


def _desde_estandarizador(cifras):
    """Lee la salida de func-emis. Las claves son los id del catálogo EMIS
    (net_sales_revenue, cost_of_goods_sold...). Montos en miles de USD; ratios de EMIS
    como fracción. No se usa resumen_financiero: repite ids de los estados con menos precisión."""
    resultado = {}

    def cargar_filas(filas):
        for fila in filas or []:
            if isinstance(fila, dict) and fila.get("id") and isinstance(fila.get("valores"), dict):
                serie = _serie_desde_dict(fila["valores"])
                if serie:
                    resultado[str(fila["id"])] = serie

    if "estados" in cifras or ("ratios" in cifras and "estados_financieros" not in cifras
                                and isinstance(cifras.get("ratios"), dict)
                                and all(isinstance(v, dict) for v in cifras["ratios"].values())):
        # Vista compacta: {"estados": {id: {anio: valor}}, "ratios": {id: {anio: valor}}}
        for bloque in ("estados", "ratios"):
            for id_, valores in (cifras.get(bloque) or {}).items():
                if isinstance(valores, dict):
                    serie = _serie_desde_dict(valores)
                    if serie:
                        resultado[str(id_)] = serie
        return resultado

    estados = cifras.get("estados_financieros") or {}
    for seccion in SECCIONES_ESTADOS:
        cargar_filas(estados.get(seccion))
    for grupo, filas in (cifras.get("ratios") or {}).items():
        if isinstance(filas, list):
            for fila in filas:
                if isinstance(fila, dict) and fila.get("id") not in resultado:
                    cargar_filas([fila])
    return resultado


def normalizar_ipc(ipc):
    serie = {}
    for fila in ipc or []:
        if isinstance(fila, dict) and fila.get("anio") is not None and _es_anio(fila["anio"]):
            n = _a_numero(fila.get("valor_pct", fila.get("valor")))
            if n is not None:
                serie[int(str(fila["anio"])[:4])] = n
    return serie


# ---------------------------------------------------------------------------
# 2. Validar y evaluar la fórmula
# ---------------------------------------------------------------------------
class ErrorFormula(Exception):
    pass


def compilar(formula):
    """Devuelve (arbol_ast, variables) o lanza ErrorFormula. variables: {nombre_interno: (clave, rezago)}"""
    if not isinstance(formula, str) or not formula.strip():
        raise ErrorFormula("formula_calculo vacía")
    variables = {}

    def reemplazo(m):
        clave, rezago = m.group(1).strip(), int(m.group(2) or 0)
        nombre = f"v{len(variables)}"
        for k, v in variables.items():
            if v == (clave, rezago):
                return k
        variables[nombre] = (clave, rezago)
        return nombre

    expresion = TOKEN.sub(reemplazo, formula)
    if "{" in expresion or "}" in expresion:
        raise ErrorFormula("Hay llaves que no siguen la sintaxis {clave} o {clave@t-1}")
    try:
        arbol = ast.parse(expresion, mode="eval")
    except SyntaxError as e:
        raise ErrorFormula(f"Sintaxis inválida: {e.msg}")

    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.Expression, ast.Load)):
            continue
        if isinstance(nodo, ast.BinOp) and isinstance(nodo.op, OPERADORES):
            continue
        if isinstance(nodo, ast.UnaryOp) and isinstance(nodo.op, (ast.USub, ast.UAdd)):
            continue
        if isinstance(nodo, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd)):
            continue
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, (int, float)) and not isinstance(nodo.value, bool):
            continue
        if isinstance(nodo, ast.Name) and nodo.id in variables:
            continue
        raise ErrorFormula(f"Elemento no permitido en la fórmula: {type(nodo).__name__}")
    if not variables:
        raise ErrorFormula("La fórmula no usa ninguna variable de las cifras")
    return arbol, variables


def constantes_inusuales(arbol):
    return sorted({n.value for n in ast.walk(arbol)
                   if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
                   and n.value not in CONSTANTES_ESPERADAS})


def _evaluar(nodo, valores):
    if isinstance(nodo, ast.Expression):
        return _evaluar(nodo.body, valores)
    if isinstance(nodo, ast.Constant):
        return float(nodo.value)
    if isinstance(nodo, ast.Name):
        return valores[nodo.id]
    if isinstance(nodo, ast.UnaryOp):
        v = _evaluar(nodo.operand, valores)
        return -v if isinstance(nodo.op, ast.USub) else v
    a, b = _evaluar(nodo.left, valores), _evaluar(nodo.right, valores)
    if isinstance(nodo.op, ast.Add):
        return a + b
    if isinstance(nodo.op, ast.Sub):
        return a - b
    if isinstance(nodo.op, ast.Mult):
        return a * b
    if b == 0:
        raise ZeroDivisionError
    return a / b


def calcular_kpi(kpi, cifras_n, ipc_n):
    base = {k: kpi.get(k) for k in ("indicador_id", "nombre", "alcance", "eje", "unidad", "formula_calculo")}
    base["alerta_ambiguedad"] = kpi.get("alerta_ambiguedad") or {"hay": False}
    base["advertencias"] = []

    if (kpi.get("estado") or "").lower() != "calculable":
        base.update(estado="omitido", motivo=kpi.get("motivo") or f"estado del agente: {kpi.get('estado')}", serie=[])
        return base

    try:
        arbol, variables = compilar(kpi.get("formula_calculo"))
    except ErrorFormula as e:
        base.update(estado="error_formula", motivo=str(e), serie=[])
        return base

    raras = constantes_inusuales(arbol)
    if raras:
        base["advertencias"].append(f"La fórmula usa constantes poco habituales {raras}: revisar que no sean cifras escritas a mano")

    def serie_de(clave):
        return ipc_n if clave.lower() == "ipc" else cifras_n.get(clave)

    faltantes = sorted({c for c, _ in variables.values() if serie_de(c) is None})
    base["conceptos_usados"] = sorted({c for c, _ in variables.values()})
    if faltantes:
        base.update(estado="sin_datos", motivo=f"Conceptos que no existen en las cifras: {faltantes}", serie=[])
        return base

    # Los años salen de las cifras; el IPC solo acompaña (no agrega años sin cifras).
    claves_cifras = [c for c, _ in variables.values() if c.lower() != "ipc"] or [c for c, _ in variables.values()]
    anios = sorted(set().union(*[set(serie_de(c).keys()) for c in claves_cifras]))
    serie = []
    for anio in anios:
        valores, falta = {}, []
        for nombre, (clave, rezago) in variables.items():
            v = serie_de(clave).get(anio - rezago)
            if v is None:
                falta.append(f"{clave}@{anio - rezago}")
            valores[nombre] = v
        if falta:
            serie.append({"anio": anio, "valor": None, "error": f"Falta {', '.join(falta)}"})
            continue
        try:
            serie.append({"anio": anio, "valor": round(_evaluar(arbol, valores), 4), "error": ""})
        except ZeroDivisionError:
            serie.append({"anio": anio, "valor": None, "error": "División por cero"})

    con_valor = [p for p in serie if p["valor"] is not None]
    base["serie"] = serie
    base["estado"] = "calculado" if con_valor else "sin_datos"
    base["motivo"] = "" if con_valor else "Ningún año tiene todos los datos que pide la fórmula"
    base["_compilado"] = (arbol, variables, serie_de)
    return base


# ---------------------------------------------------------------------------
# 3. Valor de la serie que se compara contra el scorecard (columna `serie` del catálogo)
# ---------------------------------------------------------------------------
REGLAS_SERIE = ("ultimo_año", "promedio_3_años", "cagr_3_años", "desviacion_5_años")
MINIMO_DATOS_DESVIACION = 3
UMBRAL_ESTABLE_PP = 1.0        # indicadores en %: cambio menor a 1 punto = estable
UMBRAL_ESTABLE_RELATIVO = 0.05  # demás unidades: cambio menor a 5 % = estable


def _valores_por_anio(serie):
    return {p["anio"]: p["valor"] for p in serie if p["valor"] is not None}


def _tendencia(serie, unidad, ultimo):
    valores = _valores_por_anio(serie)
    ventana = [a for a in range(ultimo - 2, ultimo + 1) if a in valores]
    if len(ventana) < 2:
        return "sin_dato"
    primero, final = valores[ventana[0]], valores[ventana[-1]]
    cambio = final - primero
    if unidad == "%":
        estable = abs(cambio) < UMBRAL_ESTABLE_PP
    else:
        estable = primero != 0 and abs(cambio / primero) < UMBRAL_ESTABLE_RELATIVO
    if estable:
        return "estable"
    return "sube" if cambio > 0 else "baja"


def _promedio_3(r, ultimo):
    arbol, variables, serie_de = r["_compilado"]
    if any(rezago for _, rezago in variables.values()):
        return None, [], "La fórmula usa @t-1: con promedio_3_años no se puede sumar por años"
    usados = []
    for anio in range(ultimo - 2, ultimo + 1):
        if all(serie_de(c).get(anio) is not None for c, _ in variables.values()):
            usados.append(anio)
    if not usados:
        return None, [], "Ningún año de la ventana tiene todos los datos"
    sumas = {n: sum(serie_de(c)[a] for a in usados) for n, (c, _) in variables.items()}
    try:
        return round(_evaluar(arbol, sumas), 4), usados, ""
    except ZeroDivisionError:
        return None, usados, "División por cero en la suma de la ventana"


def _cagr_3(r, ultimo, ipc_n):
    valores = _valores_por_anio(r["serie"])
    inicio = ultimo - 3
    if ultimo not in valores or inicio not in valores:
        return None, [], False, f"Falta el valor de {inicio if inicio not in valores else ultimo} para el CAGR"
    v0, v1 = valores[inicio], valores[ultimo]
    if v0 <= 0 or v1 <= 0:
        return None, [inicio, ultimo], False, "CAGR no definido con valores cero o negativos"
    razon = v1 / v0
    anios_ipc = list(range(ultimo - 2, ultimo + 1))
    deflactado = all(a in ipc_n for a in anios_ipc)
    if deflactado:
        for a in anios_ipc:
            razon /= (1 + ipc_n[a] / 100)
    motivo = "" if deflactado else "Sin IPC para los 3 años: CAGR nominal, sin deflactar"
    return round((razon ** (1 / 3) - 1) * 100, 4), [inicio, ultimo], deflactado, motivo


def _desviacion_5(r, ultimo):
    valores = _valores_por_anio(r["serie"])
    usados = [a for a in range(ultimo - 4, ultimo + 1) if a in valores]
    if len(usados) < MINIMO_DATOS_DESVIACION:
        return None, usados, f"Solo {len(usados)} datos en 5 años; mínimo {MINIMO_DATOS_DESVIACION}"
    return round(statistics.stdev([valores[a] for a in usados]), 4), usados, ""


def aplicar_serie(r, regla, unidad, ipc_n):
    r["serie_regla"] = regla
    r.update(valor_serie=None, anios_serie=[], ventana_incompleta=False, deflactado=None,
             tendencia_3="sin_dato", motivo_serie="")
    if r["estado"] != "calculado":
        r["motivo_serie"] = r.get("motivo", "")
        return r
    ultimo = max(p["anio"] for p in r["serie"])
    r["anio_ultimo"] = ultimo
    r["tendencia_3"] = _tendencia(r["serie"], unidad, ultimo)

    if regla == "ultimo_año":
        v = _valores_por_anio(r["serie"]).get(ultimo)
        r.update(valor_serie=v, anios_serie=[ultimo] if v is not None else [],
                 motivo_serie="" if v is not None else f"Sin valor en el último año ({ultimo})")
    elif regla == "promedio_3_años":
        v, usados, motivo = _promedio_3(r, ultimo)
        r.update(valor_serie=v, anios_serie=usados, motivo_serie=motivo,
                 ventana_incompleta=0 < len(usados) < 3)
    elif regla == "cagr_3_años":
        v, usados, deflactado, motivo = _cagr_3(r, ultimo, ipc_n)
        r.update(valor_serie=v, anios_serie=usados, deflactado=deflactado, motivo_serie=motivo)
    elif regla == "desviacion_5_años":
        v, usados, motivo = _desviacion_5(r, ultimo)
        r.update(valor_serie=v, anios_serie=usados, motivo_serie=motivo,
                 ventana_incompleta=len(usados) < 5)
    else:
        r["motivo_serie"] = f"Regla de serie desconocida: {regla}"
    return r


def _catalogo_por_id(catalogo):
    salida = {}
    for fila in catalogo or []:
        if isinstance(fila, dict) and fila.get("indicador_id"):
            salida[fila["indicador_id"]] = fila
    return salida


def calcular_todo(payload):
    cifras_n = normalizar_cifras(payload.get("cifras") or {})
    ipc_n = normalizar_ipc(payload.get("ipc"))
    if not cifras_n:
        raise ValueError("No se pudieron leer las cifras: revisa normalizar_cifras() contra la forma de cifras_emis.json")

    catalogo = _catalogo_por_id(payload.get("catalogo"))
    resultados = []
    for grupo in ("kpis", "complementarios"):
        for kpi in payload.get(grupo) or []:
            r = calcular_kpi(kpi, cifras_n, ipc_n)
            r["origen"] = "catalogo" if grupo == "kpis" else "complementario"
            fila = catalogo.get(r.get("indicador_id")) or {}
            regla = fila.get("serie") or "ultimo_año"
            unidad = fila.get("unidad") or r.get("unidad")
            if fila.get("unidad"):
                r["unidad"] = fila["unidad"]
            aplicar_serie(r, regla, unidad, ipc_n)
            r["serie_desde_catalogo"] = bool(fila.get("serie"))
            r.pop("_compilado", None)
            resultados.append(r)

    conteo = {}
    for r in resultados:
        conteo[r["estado"]] = conteo.get(r["estado"], 0) + 1
    return {
        "version_motor": VERSION_MOTOR,
        "conceptos_disponibles": sorted(cifras_n.keys()),
        "anios_disponibles": sorted({a for s in cifras_n.values() for a in s}),
        "resumen": conteo,
        "con_alerta_ambiguedad": [r["indicador_id"] for r in resultados if (r.get("alerta_ambiguedad") or {}).get("hay")],
        "kpis": resultados,
    }
