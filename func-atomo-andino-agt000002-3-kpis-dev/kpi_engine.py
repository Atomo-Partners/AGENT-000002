"""
Motor de cálculo de KPIs de AGENT-000002 · Fase 2 (versión 3).

Todo se calcula en USD, como entrega EMIS, con estas reglas fijas:
  - Las fórmulas de los indicadores del catálogo salen de config/formulas_kpi.json, no del agente.
    El agente selector decide qué indicadores aplican; el motor decide cuánto valen.
  - Si una fórmula mezcla balance con resultados o flujo, el balance se lleva a la tasa promedio del año
    (EMIS convierte el balance a la tasa de cierre), para que el tipo de cambio no distorsione el cociente.
  - El crecimiento compuesto se calcula sobre las cifras en USD y se deflacta con el IPC del país de la
    empresa (config/ipc.json; Colombia, DANE). Se marcan los años con un movimiento cambiario mayor al
    umbral de config/industrias.json, porque el crecimiento en USD incluye el efecto del tipo de cambio.
  - La serie se ancla en el último año con dato del indicador; si es anterior al último año del reporte,
    queda marcado como desfasado.

Sintaxis de las fórmulas:
    {clave}         valor de la cuenta en el año que se calcula
    {clave@t-1}     la cuenta en el año anterior (también @t-2, etc.)
    {clave?}        cuenta opcional: vale 0 si el reporte no la trae en ningún año
    {MACRO}         alternativas de config (REV, EBITDA, CXC, CXP) y {ANCLA}
    {empleados}     total de empleados de EMIS (solo en el año vigente)
    {ipc}           IPC del país de la empresa en el año, en porcentaje
    + - * / ( ) y números
"""
import ast
import itertools
import json
import math
import re
import statistics

VERSION_MOTOR = "3.2"
TOKEN = re.compile(r"\{\s*([a-z0-9_\.\-]+?)\s*(?:@\s*t\s*-\s*(\d+))?\s*(\?)?\s*\}")
TOKEN_MACRO = re.compile(r"\{\s*([A-Z][A-Z0-9_]*)\s*(?:@\s*t\s*-\s*(\d+))?\s*\}")
CONSTANTES_ESPERADAS = {0, 1, 2, 4, 12, 100, 360, 365, 1000}
OPERADORES = (ast.Add, ast.Sub, ast.Mult, ast.Div)
REGLAS_SERIE = ("ultimo_año", "promedio_3_años", "cagr_3_años", "desviacion_5_años")
MINIMO_DATOS_DESVIACION = 3
UMBRAL_ESTABLE_PP = 1.0
UMBRAL_ESTABLE_RELATIVO = 0.05
ESTADOS_SELECTOR_QUE_SE_RESPETAN = {"no_aplica", "pedir_al_target"}


class ErrorFormula(Exception):
    pass


# ---------------------------------------------------------------------------
# 1. Cifras
# ---------------------------------------------------------------------------
def como_objeto(valor):
    """Logic Apps a veces manda listas u objetos como texto JSON."""
    if isinstance(valor, str):
        try:
            return json.loads(valor)
        except ValueError:
            return valor
    return valor


def _a_numero(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    if isinstance(v, str):
        s = v.strip().replace(" ", "").replace("%", "")
        if not s:
            return None
        if "," in s and "." not in s:  # "5,62" -> 5.62 (decimal con coma)
            s = s.replace(",", ".")
        try:
            return float(s)
        except ValueError:
            return None
    if isinstance(v, dict):
        for k in ("valor", "value", "valor_pct"):
            if k in v:
                return _a_numero(v[k])
    return None


def _anio(k):
    try:
        a = int(str(k)[:4])
    except ValueError:
        return None
    return a if 1900 <= a <= 2100 else None


def _serie(d):
    serie = {}
    for k, v in (d or {}).items():
        a, n = _anio(k), _a_numero(v)
        if a is not None and n is not None:
            serie[a] = n
    return serie


class Cifras:
    """Lee cifras_compactas del estandarizador (o la salida completa, que la contiene)."""

    def __init__(self, cifras):
        cifras = como_objeto(cifras) or {}
        if isinstance(cifras, dict) and isinstance(cifras.get("cifras_compactas"), dict):
            cifras = cifras["cifras_compactas"]
        if not isinstance(cifras, dict):
            raise ValueError("cifras debe ser un objeto (cifras_compactas del estandarizador)")
        self.series, self.secciones = {}, {}
        for id_, valores in (cifras.get("estados") or {}).items():
            s = _serie(valores)
            if s:
                self.series[id_] = s
        for id_, valores in (cifras.get("ratios") or {}).items():
            s = _serie(valores)
            if s and id_ not in self.series:
                self.series[id_] = s
                self.secciones[id_] = "ratio"
        self.secciones.update(cifras.get("secciones") or {})
        self.tasas = {}
        for anio, t in (cifras.get("tasas") or {}).items():
            a = _anio(anio)
            if a is not None and isinstance(t, dict):
                self.tasas[a] = {k: _a_numero(v) for k, v in t.items()}
        self.empleados = cifras.get("empleados") if isinstance(cifras.get("empleados"), dict) else None
        anios = [_anio(a) for a in (cifras.get("anios") or [])]
        self.anios = sorted({a for a in anios if a} or {a for s in self.series.values() for a in s})
        self.anio_reporte = max(self.anios) if self.anios else None
        self.calidad = cifras.get("calidad") or {}
        if not self.series:
            raise ValueError("No se pudieron leer las cifras: se espera cifras_compactas del estandarizador")

    def existe(self, clave):
        return clave in self.series

    def tiene_secciones(self):
        return bool(self.secciones)

    def movimientos_cambiarios(self, umbral_pct):
        """Años en que la tasa promedio de EMIS se movió más que el umbral frente al año anterior."""
        salida = []
        for a in sorted(self.tasas):
            actual, previa = (self.tasas.get(a) or {}).get("resultados"), (self.tasas.get(a - 1) or {}).get("resultados")
            if actual and previa:
                variacion = (actual / previa - 1) * 100
                if abs(variacion) > umbral_pct:
                    salida.append({"anio": a, "variacion_tasa_pct": round(variacion, 2)})
        return salida

    def serie_empleados(self, vigencia):
        """{anio: empleados} si el conteo está a 'vigencia' años o menos del último año del reporte.
        El valor se asigna al último año del reporte, que es el que usa la serie ultimo_año."""
        if not self.empleados or self.empleados.get("valor") is None:
            return None, "EMIS no trae el total de empleados"
        anio = self.empleados.get("anio")
        if anio is None:
            return None, f"EMIS trae empleados sin año ('{self.empleados.get('texto')}')"
        if self.anio_reporte is None or abs(anio - self.anio_reporte) > vigencia:
            return None, (f"El conteo de empleados de EMIS es de {anio} y las cifras llegan a {self.anio_reporte}: "
                          "dato desactualizado")
        return {self.anio_reporte: float(self.empleados["valor"])}, ""


# ---------------------------------------------------------------------------
# 2. Fórmulas: macros, compilación y evaluación
# ---------------------------------------------------------------------------
def _aplicar_rezago(expresion, rezago):
    if not rezago:
        return expresion

    def sumar(m):
        clave, r, opcional = m.group(1), int(m.group(2) or 0), m.group(3) or ""
        return "{" + f"{clave}@t-{r + rezago}{opcional}" + "}"

    def sumar_macro(m):
        r = int(m.group(2) or 0)
        return "{" + f"{m.group(1)}@t-{r + rezago}" + "}"

    return TOKEN_MACRO.sub(sumar_macro, TOKEN.sub(sumar, expresion))


def expandir(formula, macros, ancla_expr):
    """Devuelve las fórmulas candidatas (sin macros), en orden de preferencia."""
    macros = dict(macros or {})
    macros["ANCLA"] = [ancla_expr]
    candidatas = [formula]
    for _ in range(4):  # las macros pueden contener otras macros (ANCLA -> EBITDA)
        nuevas = []
        for f in candidatas:
            nombres = list(dict.fromkeys(m.group(1) for m in TOKEN_MACRO.finditer(f)))
            if not nombres:
                nuevas.append(f)
                continue
            desconocidas = [n for n in nombres if n not in macros]
            if desconocidas:
                raise ErrorFormula(f"Macro desconocida: {desconocidas}")
            for combo in itertools.product(*[macros[n] for n in nombres]):
                elegida = dict(zip(nombres, combo))
                nuevas.append(TOKEN_MACRO.sub(
                    lambda m: "(" + _aplicar_rezago(elegida[m.group(1)], int(m.group(2) or 0)) + ")", f))
        candidatas = nuevas
    if any(TOKEN_MACRO.search(f) for f in candidatas):
        raise ErrorFormula("Macros anidadas sin resolver")
    return list(dict.fromkeys(candidatas))


def compilar(formula):
    """Devuelve (arbol_ast, variables) o lanza ErrorFormula. variables: {nombre: (clave, rezago, opcional)}"""
    if not isinstance(formula, str) or not formula.strip():
        raise ErrorFormula("formula_calculo vacía")
    variables = {}

    def reemplazo(m):
        llave = (m.group(1).strip(), int(m.group(2) or 0), bool(m.group(3)))
        for nombre, v in variables.items():
            if v == llave:
                return nombre
        nombre = f"v{len(variables)}"
        variables[nombre] = llave
        return nombre

    expresion = TOKEN.sub(reemplazo, formula)
    if "{" in expresion or "}" in expresion:
        raise ErrorFormula("Hay llaves que no siguen la sintaxis {clave}, {clave@t-1} o {clave?}")
    try:
        arbol = ast.parse(expresion, mode="eval")
    except SyntaxError as e:
        raise ErrorFormula(f"Sintaxis inválida: {e.msg}")
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.Expression, ast.Load, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd)):
            continue
        if isinstance(nodo, ast.BinOp) and isinstance(nodo.op, OPERADORES):
            continue
        if isinstance(nodo, ast.UnaryOp) and isinstance(nodo.op, (ast.USub, ast.UAdd)):
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


class Contexto:
    """Todo lo que el motor necesita además de la fórmula."""

    def __init__(self, cifras, macros, ancla_expr, ipc_por_anio, umbral_cambiario, vigencia_empleados,
                 ipc_ficha=None):
        self.cifras = cifras
        self.macros = macros or {}
        self.ancla_expr = ancla_expr
        self.ipc = ipc_por_anio or {}
        self.ipc_ficha = ipc_ficha or {}
        self.umbral_cambiario = umbral_cambiario
        self.empleados, self.motivo_empleados = cifras.serie_empleados(vigencia_empleados)

    def serie(self, clave):
        if clave == "ipc":
            return self.ipc
        if clave == "empleados":
            return self.empleados
        return self.cifras.series.get(clave)

    def existe(self, clave):
        return self.serie(clave) is not None


def _faltantes(variables, ctx, minimo_uno_de=None):
    """Cuentas que faltan. 'minimo_uno_de': lista de cuentas de las que al menos una debe existir
    (p. ej. deuda corriente o no corriente). Si es None (fórmulas de agentes), se exige que exista al
    menos una de las opcionales de la fórmula."""
    obligatorias = sorted({c for c, _, opc in variables.values() if not opc and not ctx.existe(c)})
    grupo = minimo_uno_de
    if grupo is None:
        grupo = sorted({c for c, _, opc in variables.values() if opc})
    if grupo and not any(ctx.existe(c) for c in grupo):
        obligatorias.append("al menos una de " + str(sorted(set(grupo))))
    return obligatorias


def _elegir_candidata(formula, ctx, minimo_uno_de=None):
    """Primera fórmula candidata cuyas cuentas existen. Devuelve (formula, arbol, variables, faltantes)."""
    candidatas = expandir(formula, ctx.macros, ctx.ancla_expr)
    primera = None
    for f in candidatas:
        arbol, variables = compilar(f)
        faltan = _faltantes(variables, ctx, minimo_uno_de)
        if primera is None:
            primera = (f, arbol, variables, faltan)
        if not faltan:
            return f, arbol, variables, []
    return primera


def _mezcla_balance(variables, ctx):
    secciones = {ctx.cifras.secciones.get(c) for c, _, _ in variables.values() if ctx.existe(c)}
    return "balance" in secciones and bool(secciones & {"resultados", "flujo"})


def _valores_anio(anio, variables, ctx, ajustar):
    """Valores de las variables en un año, con el balance llevado a la tasa promedio si 'ajustar'.
    Cada saldo se lleva a la tasa promedio de su propio año (el de @t-1, a la del año anterior), igual que
    EMIS convierte los resultados: así un crecimiento de balance y uno de resultados quedan en la misma
    moneda."""
    valores, falta, sin_tasa = {}, [], False
    for nombre, (clave, rezago, opcional) in variables.items():
        serie = ctx.serie(clave)
        if serie is None and opcional:
            valores[nombre] = 0.0
            continue
        v = (serie or {}).get(anio - rezago)
        if v is None:
            falta.append(f"{clave}@{anio - rezago}")
            continue
        if ajustar and ctx.cifras.secciones.get(clave) == "balance":
            cierre = (ctx.cifras.tasas.get(anio - rezago) or {}).get("balance")
            promedio = (ctx.cifras.tasas.get(anio - rezago) or {}).get("resultados")
            if cierre and promedio:
                v = v * cierre / promedio
            else:
                sin_tasa = True
        valores[nombre] = v
    return valores, falta, sin_tasa


def _finito(v):
    return v if v is not None and math.isfinite(v) else None


def _marcar_denominador(serie, por_anio, denominador, ctx, base):
    """Años en que el denominador es cero o negativo: el cociente no significa nada (deuda / EBITDA
    negativo da un múltiplo negativo que parecería bueno). Esos años quedan sin valor y marcados."""
    try:
        _, d_arbol, d_vars, d_faltan = _elegir_candidata(denominador, ctx, [])
    except ErrorFormula as e:
        base["advertencias"].append(f"No se pudo revisar el signo del denominador: {e}")
        return
    if d_faltan:
        return
    for p in serie:
        if p["valor"] is None:
            continue
        valores, falta, _ = _valores_anio(p["anio"], d_vars, ctx, False)
        if falta:
            continue
        try:
            d = _evaluar(d_arbol, valores)
        except ZeroDivisionError:
            continue
        if d <= 0:
            p.update(valor=None, no_significativo=True,
                     error=f"Denominador {round(d, 2)} (cero o negativo): el indicador no es significativo")
            por_anio.pop(p["anio"], None)


def calcular_formula(formula, ctx, base, minimo_uno_de=None, denominador=None):
    """Calcula la serie anual de una fórmula. Completa 'base' y devuelve los datos para aplicar la serie.
    'denominador': expresión que debe ser positiva para que el cociente tenga sentido."""
    try:
        f, arbol, variables, faltan = _elegir_candidata(formula, ctx, minimo_uno_de)
    except ErrorFormula as e:
        base.update(estado="error_formula", motivo=str(e), serie=[])
        return None
    base["formula_calculo"] = f
    base["conceptos_usados"] = sorted({c for c, _, _ in variables.values()})
    raras = constantes_inusuales(arbol)
    if raras:
        base["advertencias"].append(f"La fórmula usa constantes poco habituales {raras}: revisar que no sean cifras escritas a mano")
    if faltan:
        base.update(estado="sin_datos", motivo=f"Conceptos que no existen en las cifras: {faltan}", serie=[])
        return None

    ajustar = _mezcla_balance(variables, ctx)
    base["ajuste_tasa_balance"] = ajustar
    claves_anios = [c for c, _, opc in variables.values() if c not in ("ipc",) and ctx.existe(c)]
    anios = sorted(set().union(*[set(ctx.serie(c)) for c in claves_anios])) if claves_anios else []
    serie, por_anio, sin_tasa_total = [], {}, False
    for anio in anios:
        valores, falta, sin_tasa = _valores_anio(anio, variables, ctx, ajustar)
        sin_tasa_total = sin_tasa_total or sin_tasa
        if falta:
            serie.append({"anio": anio, "valor": None, "error": f"Falta {', '.join(falta)}"})
            continue
        try:
            valor = _finito(_evaluar(arbol, valores))
            serie.append({"anio": anio, "valor": None if valor is None else round(valor, 4),
                          "error": "" if valor is not None else "Resultado no finito"})
            if valor is not None:
                por_anio[anio] = valores
        except ZeroDivisionError:
            serie.append({"anio": anio, "valor": None, "error": "División por cero"})
    if denominador:
        _marcar_denominador(serie, por_anio, denominador, ctx, base)
    if ajustar and not ctx.cifras.tiene_secciones():
        base["advertencias"].append("Las cifras no traen 'secciones': no se pudo ajustar el balance a la tasa promedio")
    if sin_tasa_total:
        base["advertencias"].append("Faltan tasas de EMIS en algún año: ese año queda sin ajuste de tasa")
    con_valor = [p for p in serie if p["valor"] is not None or p.get("no_significativo")]
    base["serie"] = serie
    base["estado"] = "calculado" if con_valor else "sin_datos"
    base["motivo"] = "" if con_valor else "Ningún año tiene todos los datos que pide la fórmula"
    return {"arbol": arbol, "variables": variables, "por_anio": por_anio}


# ---------------------------------------------------------------------------
# 3. Valor de la serie que se compara contra el scorecard
# ---------------------------------------------------------------------------
def _valores(serie):
    return {p["anio"]: p["valor"] for p in serie if p["valor"] is not None}


def _tendencia(valores, unidad, ultimo):
    ventana = [a for a in range(ultimo - 2, ultimo + 1) if a in valores]
    if len(ventana) < 2:
        return "sin_dato"
    primero, final = valores[ventana[0]], valores[ventana[-1]]
    cambio = final - primero
    if unidad == "%":
        estable = abs(cambio) < UMBRAL_ESTABLE_PP
    else:
        estable = primero != 0 and abs(cambio / primero) < UMBRAL_ESTABLE_RELATIVO
    return "estable" if estable else ("sube" if cambio > 0 else "baja")


def _promedio_3(r, calc, ultimo):
    valores = _valores(r["serie"])
    ventana = [a for a in range(ultimo - 2, ultimo + 1) if a in valores]
    if not ventana:
        return None, [], "Ningún año de la ventana tiene valor", False
    if any(rez for _, rez, _ in calc["variables"].values()):
        # Con @t-1 no se pueden sumar cuentas por año: promedio simple de los valores anuales.
        return round(statistics.mean(valores[a] for a in ventana), 4), ventana, "", True
    sumas = {n: sum(calc["por_anio"][a][n] for a in ventana) for n in calc["variables"]}
    try:
        return round(_evaluar(calc["arbol"], sumas), 4), ventana, "", False
    except ZeroDivisionError:
        return None, ventana, "División por cero en la suma de la ventana", False


def _cagr_3(r, ultimo, ctx):
    valores = _valores(r["serie"])
    inicio = ultimo - 3
    if inicio not in valores:
        return None, [], None, f"Falta el valor de {inicio} para el crecimiento de 3 años", []
    v0, v1 = valores[inicio], valores[ultimo]
    if v0 <= 0 or v1 <= 0:
        return None, [inicio, ultimo], None, "Crecimiento no definido con valores cero o negativos", []
    razon = v1 / v0
    anios_ipc = list(range(ultimo - 2, ultimo + 1))
    deflactado = all(a in ctx.ipc for a in anios_ipc)
    if deflactado:
        for a in anios_ipc:
            razon /= (1 + ctx.ipc[a] / 100)
    pais = ctx.ipc_ficha.get("pais") or "el país de la empresa"
    motivo = "" if deflactado else f"Sin IPC de {pais} para {anios_ipc}: crecimiento nominal en USD"
    cambiarios = [m for m in ctx.cifras.movimientos_cambiarios(ctx.umbral_cambiario) if inicio < m["anio"] <= ultimo]
    if (getattr(ctx, "crecimiento", None) or {}).get("medida") == "acumulado":
        valor = razon - 1  # año final / año inicial - 1 (decisión de Átomo, 2026-11-24)
    else:
        valor = razon ** (1 / 3) - 1  # crecimiento compuesto anual
    return round(valor * 100, 4), [inicio, ultimo], deflactado, motivo, cambiarios


def _crecimiento_real_anual(r, ctx):
    """Crecimiento real de cada año contra el anterior, con el mismo deflactor del compuesto. El compuesto de 3
    años puede esconder un freno reciente (Rentek: compuesto 10,5 %, pero 2024 +2,2 % y 2025 +1,1 %); esta
    serie lo deja a la vista sin cambiar la banda del catálogo."""
    valores = _valores(r["serie"])
    salida = []
    for anio in sorted(valores):
        previo = valores.get(anio - 1)
        if previo is None or previo <= 0 or valores[anio] <= 0 or anio not in ctx.ipc:
            continue
        real = (valores[anio] / previo) / (1 + ctx.ipc[anio] / 100) - 1
        salida.append({"anio": anio, "valor": round(real * 100, 4)})
    return salida


def _desviacion_5(r, ultimo):
    valores = _valores(r["serie"])
    usados = [a for a in range(ultimo - 4, ultimo + 1) if a in valores]
    if len(usados) < MINIMO_DATOS_DESVIACION:
        return None, usados, f"Solo {len(usados)} datos en 5 años; mínimo {MINIMO_DATOS_DESVIACION}"
    return round(statistics.stdev([valores[a] for a in usados]), 4), usados, ""


def _rezago_maximo(calc):
    return max((rez for _, rez, _ in (calc or {}).get("variables", {}).values()), default=0)


def aplicar_serie(r, calc, regla, unidad, ctx):
    r["serie_regla"] = regla
    r.update(valor_serie=None, anios_serie=[], anio_ultimo=None, anio_desfasado=False, ventana_incompleta=False,
             promedio_simple=False, deflactado=None, movimientos_cambiarios=[], tendencia_3="sin_dato",
             motivo_serie="", no_significativo=False)
    if r["estado"] != "calculado":
        r["motivo_serie"] = r.get("motivo", "")
        return r
    valores = _valores(r["serie"])
    no_signif = {p["anio"] for p in r["serie"] if p.get("no_significativo")}
    ultimo = max(set(valores) | no_signif)  # último año con dato del indicador
    r["anio_ultimo"] = ultimo
    r["anio_desfasado"] = ctx.cifras.anio_reporte is not None and ultimo < ctx.cifras.anio_reporte
    if r["anio_desfasado"]:
        r["advertencias"].append(f"El último dato es de {ultimo} y el reporte llega a {ctx.cifras.anio_reporte}")
    r["tendencia_3"] = _tendencia(valores, unidad, ultimo)

    ventana = [ultimo] if regla == "ultimo_año" else list(range(ultimo - 2, ultimo + 1))
    afectados = sorted(a for a in ventana if a in no_signif)
    if afectados:
        # No se salta al año anterior: un EBITDA o patrimonio negativo es la señal, no un hueco de datos.
        r.update(valor_serie=None, no_significativo=True, anios_serie=afectados,
                 motivo_serie=f"Denominador cero o negativo en {afectados}: indicador no significativo")
        return r

    if regla == "ultimo_año":
        r.update(valor_serie=valores[ultimo], anios_serie=[ultimo])
    elif regla == "promedio_3_años":
        v, usados, motivo, simple = _promedio_3(r, calc, ultimo)
        r.update(valor_serie=v, anios_serie=usados, motivo_serie=motivo, promedio_simple=simple,
                 ventana_incompleta=0 < len(usados) < 3)
    elif regla == "cagr_3_años":
        v, usados, deflactado, motivo, cambiarios = _cagr_3(r, ultimo, ctx)
        r.update(valor_serie=v, anios_serie=usados, deflactado=deflactado, motivo_serie=motivo,
                 movimientos_cambiarios=cambiarios)
        if cambiarios:
            r["advertencias"].append("Movimiento cambiario fuerte en la ventana: el crecimiento en USD incluye el "
                                     f"efecto del tipo de cambio {cambiarios}")
        reglas_crec = getattr(ctx, "crecimiento", None) or {}
        r["medida_crecimiento"] = "acumulado" if reglas_crec.get("medida") == "acumulado" else "compuesto_anual"
        if reglas_crec.get("nota_moneda"):
            r["nota_moneda"] = reglas_crec["nota_moneda"]
        if reglas_crec.get("aviso_rangos") and r["medida_crecimiento"] == "acumulado":
            r["advertencias"].append(reglas_crec["aviso_rangos"])
        anual = _crecimiento_real_anual(r, ctx)
        r["crecimiento_real_anual"] = anual
        r["crecimiento_real_ultimo_anio"] = next((p["valor"] for p in anual if p["anio"] == ultimo), None)
        if deflactado:
            r["deflactor"] = {k: ctx.ipc_ficha.get(k) for k in ("pais", "medida", "estado", "fuente")}
            if ctx.ipc_ficha.get("estado") == "provisional":
                r["advertencias"].append(f"IPC de {ctx.ipc_ficha.get('pais')} provisional: falta el empalme con la "
                                         "serie oficial del DANE (config/ipc.json)")
    elif regla == "desviacion_5_años":
        v, usados, motivo = _desviacion_5(r, ultimo)
        # Con @t-1 (crecimiento anual) 5 años de cifras dan 4 datos: la ventana completa es 5 - rezago.
        esperados = 5 - _rezago_maximo(calc)
        r.update(valor_serie=v, anios_serie=usados, motivo_serie=motivo, ventana_incompleta=len(usados) < esperados)
    else:
        r["motivo_serie"] = f"Regla de serie desconocida: {regla}"

    # Controles de escala y signo: no cambian el valor, quedan como advertencia.
    if unidad == "%" and valores and all(abs(v) <= 1.5 for v in valores.values()) and "100" not in (r.get("formula_calculo") or ""):
        r["advertencias"].append("Indicador en % con valores entre -1,5 y 1,5: posible fracción sin multiplicar por 100")
    if unidad == "dias" and r["valor_serie"] is not None and r["valor_serie"] < 0:
        r["advertencias"].append("Días negativos: revisar signos de la fórmula")
    return r


# ---------------------------------------------------------------------------
# 4. Orquestación
# ---------------------------------------------------------------------------
def _usa_ancla(definicion):
    textos = [definicion.get("formula") or "", definicion.get("denominador_positivo") or ""]
    return any("{ANCLA" in x for x in textos + list(definicion.get("alternativas") or []))


def _valor_con_ancla(definicion, ind, ctx_ancla, ancla):
    """El mismo indicador con otra métrica ancla (la del plan). No tiene banda: es para leer al lado."""
    r = {"advertencias": [], "alerta_ambiguedad": {"hay": False}}
    calc = None
    for i, f in enumerate([definicion["formula"]] + list(definicion.get("alternativas") or [])):
        intento = dict(r, advertencias=[])
        calc = calcular_formula(f, ctx_ancla, intento, definicion.get("minimo_uno_de") or [],
                                definicion.get("denominador_positivo"))
        if intento["estado"] == "calculado" or i == len(definicion.get("alternativas") or []):
            r = intento
            break
    aplicar_serie(r, calc, ind.get("serie") or "ultimo_año", ind.get("unidad"), ctx_ancla)
    return {"metrica_ancla": ancla, "valor_serie": r.get("valor_serie"), "anios_serie": r.get("anios_serie"),
            "no_significativo": r.get("no_significativo", False), "formula_calculo": r.get("formula_calculo"),
            "motivo": r.get("motivo_serie") or r.get("motivo") or ""}


def _resolver_alerta(r, ctx):
    """Las alertas de ambigüedad del selector se copian al resultado. Cuando la Function ya resolvió el
    problema que la alerta describe, queda marcada como resuelta para que el scorecard no la muestre como
    pendiente (la decisión del selector no se aplicó)."""
    alerta = r.get("alerta_ambiguedad") or {}
    if not alerta.get("hay"):
        return
    resolucion = None
    if r["estado"] in ("no_aplica", "pedir_al_target") and r.get("estado_selector") != r["estado"]:
        resolucion = f"La Function marcó el indicador como {r['estado']}: {r.get('motivo')}"
    elif alerta.get("tipo") == "moneda":
        if r.get("ajuste_tasa_balance"):
            resolucion = ("Decisión de Átomo: análisis en USD. La Function llevó el balance a la tasa promedio de "
                          "cada año antes de dividir, así que el cociente no mezcla tasas.")
        elif r.get("serie_regla") == "cagr_3_años":
            resolucion = (f"Decisión de Átomo: crecimiento sobre cifras en USD deflactado con el IPC de "
                          f"{ctx.ipc_ficha.get('pais')}.")
        else:
            resolucion = "Decisión de Átomo: análisis en USD con las cifras de EMIS."
    if resolucion:
        r["alerta_ambiguedad"] = dict(alerta, resuelta=True, resolucion=resolucion)
    elif r.get("origen") == "catalogo" and r.get("formula_calculo"):
        r["alerta_ambiguedad"] = dict(alerta, resuelta=False,
                                      formula_aplicada=f"La Function usó la fórmula fija del catálogo: "
                                                       f"{r['formula_calculo']}")
def _base(indicador_id, fuente, kpi_selector):
    base = {k: fuente.get(k) for k in ("nombre", "alcance", "eje", "unidad")}
    base["indicador_id"] = indicador_id
    base["alerta_ambiguedad"] = (kpi_selector or {}).get("alerta_ambiguedad") or {"hay": False}
    base["estado_selector"] = (kpi_selector or {}).get("estado")
    base["formula_selector"] = (kpi_selector or {}).get("formula_calculo")
    base["advertencias"] = []
    return base


def calcular_todo(payload, cfg):
    """payload: cifras (cifras_compactas), industria, kpis y complementarios del selector.
    Los campos 'catalogo' e 'ipc' de versiones anteriores se ignoran: salen de config."""
    cifras = Cifras(payload.get("cifras"))
    industria = cfg.industria_valida(payload.get("industria"))
    ancla, ancla_expr = cfg.expresion_ancla(industria)
    ipc_serie, ipc_ficha = cfg.ipc_de_pais(payload.get("pais"))
    ctx = Contexto(cifras, cfg.formulas.get("macros"), ancla_expr, ipc_serie, cfg.umbral_cambiario,
                   cfg.vigencia_empleados, ipc_ficha)
    ctx.crecimiento = cfg.formulas.get("crecimiento") or {}
    # Si el plan eligió otra métrica ancla, los indicadores que la usan se calculan también con ella.
    ancla_plan, ancla_plan_expr = cfg.ancla_desde_texto(payload.get("metrica_ancla_plan"))
    ctx_plan = None
    if ancla_plan and ancla_plan != ancla:
        ctx_plan = Contexto(cifras, cfg.formulas.get("macros"), ancla_plan_expr, ipc_serie, cfg.umbral_cambiario,
                            cfg.vigencia_empleados, ipc_ficha)

    selector = {}
    for k in como_objeto(payload.get("kpis")) or []:
        if isinstance(k, dict) and k.get("indicador_id"):
            selector[k["indicador_id"]] = k

    resultados, ignorados = [], []
    alcance = cfg.kpis_en_alcance(industria)
    ids_alcance = {i["indicador_id"] for i in alcance}
    for ind in alcance:
        id_ = ind["indicador_id"]
        k_sel = selector.get(id_)
        r = _base(id_, ind, k_sel)
        r["origen"] = "catalogo"
        definicion = cfg.formulas["kpis"].get(id_)
        estado_sel = (k_sel or {}).get("estado")
        if k_sel is None:
            r["advertencias"].append("El selector no listó este indicador; se calcula igual porque está en el alcance")
        calc = None
        if estado_sel in ESTADOS_SELECTOR_QUE_SE_RESPETAN:
            r.update(estado=estado_sel, motivo=(k_sel.get("motivo") or f"Decisión del selector: {estado_sel}"),
                     serie=[], formula_calculo=None)
        elif definicion is None:
            r.update(estado="sin_formula", motivo="El indicador no tiene fórmula en config/formulas_kpi.json",
                     serie=[], formula_calculo=None)
        else:
            falta_negocio = [c for c in definicion.get("no_aplica_si_falta") or [] if not ctx.existe(c)]
            requiere_emp = "empleados" in (definicion.get("requiere") or [])
            pedir = definicion.get("pedir_si_falta") or []
            if falta_negocio:
                r.update(estado="no_aplica", serie=[], formula_calculo=None,
                         motivo=f"La empresa no reporta {falta_negocio}: el indicador no aplica a este negocio")
            elif pedir and not any(ctx.existe(c) for c in pedir):
                r.update(estado="pedir_al_target", serie=[], formula_calculo=None,
                         motivo=f"EMIS no trae {pedir}, que el indicador necesita: pedirlo al target")
            elif requiere_emp and ctx.empleados is None:
                r.update(estado="pedir_al_target", serie=[], formula_calculo=None, motivo=ctx.motivo_empleados)
            else:
                formulas = [definicion["formula"]] + list(definicion.get("alternativas") or [])
                for i, f in enumerate(formulas):
                    intento = dict(r, advertencias=list(r["advertencias"]))
                    calc = calcular_formula(f, ctx, intento, definicion.get("minimo_uno_de") or [],
                                            definicion.get("denominador_positivo"))
                    if intento["estado"] == "calculado" or i == len(formulas) - 1:
                        r = intento
                        break
                if definicion.get("nota"):
                    r["nota_formula"] = definicion["nota"]
                if estado_sel == "sin_datos" and r["estado"] == "calculado":
                    r["advertencias"].append("El selector lo marcó sin datos, pero las cifras sí permiten calcularlo")
        aplicar_serie(r, calc, ind.get("serie") or "ultimo_año", ind.get("unidad"), ctx)
        for clave, texto in ((definicion or {}).get("advertir_si_cero") or {}).items():
            serie = ctx.serie(clave) or {}
            ceros = [a for a in r.get("anios_serie") or [] if not serie.get(a)]
            if r["estado"] == "calculado" and ceros:
                r["advertencias"].append(f"{clave} es cero o falta en {ceros}: {texto}")
        if ctx_plan and definicion and r["estado"] == "calculado" and _usa_ancla(definicion):
            r["valor_ancla_plan"] = _valor_con_ancla(definicion, ind, ctx_plan, ancla_plan)
        _resolver_alerta(r, ctx)
        resultados.append(r)

    for id_ in selector:
        if id_ not in ids_alcance:
            ignorados.append({"indicador_id": id_, "motivo": "No es general ni de la industria del análisis"})

    for comp in como_objeto(payload.get("complementarios")) or []:
        if not isinstance(comp, dict) or not comp.get("indicador_id"):
            continue
        r = _base(comp["indicador_id"], comp, comp)
        r["origen"] = "complementario"
        r["brecha_que_cierra"] = comp.get("brecha_que_cierra")
        calc = None
        if (comp.get("estado") or "calculable").lower() != "calculable":
            r.update(estado="omitido", motivo=comp.get("motivo") or f"estado del agente: {comp.get('estado')}",
                     serie=[], formula_calculo=comp.get("formula_calculo"))
        else:
            calc = calcular_formula(comp.get("formula_calculo"), ctx, r)
        regla = comp.get("serie") if comp.get("serie") in REGLAS_SERIE else "ultimo_año"
        aplicar_serie(r, calc, regla, comp.get("unidad"), ctx)
        resultados.append(r)

    conteo = {}
    for r in resultados:
        conteo[r["estado"]] = conteo.get(r["estado"], 0) + 1
    return {
        "version_motor": VERSION_MOTOR,
        "version_config": cfg.version(),
        "moneda_calculo": "usd",
        "deflactor": (f"IPC de {ipc_ficha['pais']} ({ipc_ficha.get('fuente')}; {ipc_ficha.get('medida')}; "
                      f"{ipc_ficha.get('estado')}) sobre cifras en USD") if ipc_ficha.get("pais")
                     else f"Sin deflactor: {ipc_ficha.get('motivo')}",
        "ipc": ipc_ficha,
        "industria": industria,
        "metrica_ancla": ancla,
        "metrica_ancla_plan": ancla_plan,
        "anio_reporte": cifras.anio_reporte,
        "conceptos_disponibles": sorted(cifras.series),
        "anios_disponibles": cifras.anios,
        "movimientos_cambiarios": cifras.movimientos_cambiarios(cfg.umbral_cambiario),
        "resumen": conteo,
        "con_alerta_ambiguedad": [r["indicador_id"] for r in resultados
                                  if (r.get("alerta_ambiguedad") or {}).get("hay")
                                  and not (r.get("alerta_ambiguedad") or {}).get("resuelta")],
        "ignorados": ignorados,
        "kpis": resultados,
    }
