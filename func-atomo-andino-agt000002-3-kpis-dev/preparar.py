"""Contexto determinístico del análisis (ruta preparar_analisis).

Corre justo después del estandarizador y antes de los agentes. Decide, sin LLM:
  - la industria del catálogo a partir del Sector priorizado de Notion, y su métrica ancla;
  - si hay un bloqueo de datos grave que obliga a detener el flujo;
  - el catálogo, las anclas por industria y las cifras con moneda_calculo, para los agentes;
  - la moneda local (de EMIS o del país del deal) y el IPC del país con que se deflacta el crecimiento.

Bloqueo grave (detiene el flujo):
  1. no hay ingresos en ningún año;
  2. la métrica ancla de la industria no se puede armar en ningún año;
  3. hay menos de 2 años con estado de resultados (ingresos) y balance (activos totales).
Cualquier otro problema de calidad sigue con marca.
"""
from kpi_engine import Cifras, Contexto, ErrorFormula, calcular_formula, como_objeto

ANIOS_MINIMOS = 2


def _cifras_para_agentes(cifras_compactas, moneda_local, ipc_ficha, ipc_serie):
    salida = dict(como_objeto(cifras_compactas) or {})
    salida["moneda_calculo"] = "usd"
    salida["moneda_local"] = moneda_local
    salida["ipc_calculo"] = dict(ipc_ficha, por_anio={str(a): v for a, v in sorted(ipc_serie.items())},
                                 uso="La Function deflacta el crecimiento de las cifras en USD con este IPC")
    return salida


def _moneda_local(cifras_raw, pais, cfg):
    """Moneda en que reporta la empresa. EMIS en español a veces no trae la fila de unidades originales
    (Rentek): entonces se toma la moneda del país del deal en config/ipc.json."""
    original = (cifras_raw or {}).get("moneda_original")
    codigo_emis = str(original).split("_")[0].upper() if original else None
    codigo_pais = cfg.moneda_de_pais(pais)
    if codigo_emis:
        salida = {"codigo": codigo_emis, "origen": "EMIS (unidades originales del reporte)", "advertencia": ""}
        if codigo_pais and codigo_pais != codigo_emis:
            salida["advertencia"] = (f"EMIS reporta {codigo_emis} y el país del deal usa {codigo_pais}: revisar "
                                     "qué IPC corresponde")
        return salida
    if codigo_pais:
        return {"codigo": codigo_pais, "origen": f"País del deal ({pais or cfg.ipc['pais_por_defecto']}); "
                                                 "EMIS no trae la fila de unidades originales",
                "advertencia": ""}
    return {"codigo": None, "origen": "", "advertencia": "No se pudo determinar la moneda local"}


def preparar(payload, cfg):
    sector = payload.get("sector_priorizado") or payload.get("sector")
    cifras_raw = como_objeto(payload.get("cifras"))
    if isinstance(cifras_raw, dict) and isinstance(cifras_raw.get("cifras_compactas"), dict):
        cifras_raw = cifras_raw["cifras_compactas"]
    cifras = Cifras(cifras_raw)

    industria = cfg.industria_de_sector(sector)
    if industria:
        industria_catalogo = {"valor": industria, "tipo": "exacta", "metrica_ancla_catalogo": cfg.metrica_ancla(industria),
                              "justificacion": f"Sector priorizado de Notion '{sector}' traducido con la tabla fija "
                                               "de config/industrias.json. Es la industria del sector, no confirma que la empresa "
                                               "pertenezca a ella: el selector la confirma con la evidencia o la marca como proxy"}
    else:
        industria_catalogo = {"valor": None, "tipo": "ninguna", "metrica_ancla_catalogo": None,
                              "justificacion": f"El Sector priorizado '{sector}' no está en la tabla de "
                                               "config/industrias.json; el planificador propone la industria"}

    ancla, ancla_expr = cfg.expresion_ancla(industria)
    pais = payload.get("pais")
    ipc_serie, ipc_ficha = cfg.ipc_de_pais(pais)
    moneda_local = _moneda_local(cifras_raw, pais, cfg)
    ctx = Contexto(cifras, cfg.formulas.get("macros"), ancla_expr, ipc_serie, cfg.umbral_cambiario,
                   cfg.vigencia_empleados, ipc_ficha)

    def anios_con_valor(formula):
        base = {"advertencias": []}
        try:
            calcular_formula(formula, ctx, base)
        except ErrorFormula:
            return []
        return [p["anio"] for p in base.get("serie") or [] if p["valor"] is not None]

    motivos = []
    anios_ingresos = anios_con_valor("{REV}")
    if not anios_ingresos:
        motivos.append("No hay ingresos en ningún año")
    if not anios_con_valor("{ANCLA}"):
        motivos.append(f"La métrica ancla ({ancla}) no se puede armar en ningún año")
    activos = cifras.series.get("total_assets") or {}
    anios_completos = [a for a in anios_ingresos if a in activos]
    if len(anios_completos) < ANIOS_MINIMOS:
        motivos.append(f"Solo {len(anios_completos)} años con ingresos y activos totales; mínimo {ANIOS_MINIMOS}")

    calidad = cifras.calidad or {}
    return {
        "version_config": cfg.version(),
        "industria_catalogo": industria_catalogo,
        "industrias_catalogo": cfg.industrias,
        "anclas_por_industria": cfg.anclas_por_industria(),
        "catalogo": cfg.catalogo_para_agentes(),
        "bloqueo": {"grave": bool(motivos), "motivos": motivos},
        "calidad": {"estado": calidad.get("estado"),
                    "cuentas_clave_faltantes": calidad.get("cuentas_clave_faltantes") or [],
                    "fallas": calidad.get("fallas") or {}},
        "movimientos_cambiarios": cifras.movimientos_cambiarios(cfg.umbral_cambiario),
        "empleados": {"vigente": ctx.empleados is not None, "motivo": ctx.motivo_empleados,
                      "dato": cifras.empleados},
        "moneda_calculo": "usd",
        "moneda_local": moneda_local,
        "ipc": ipc_ficha,
        "cifras_agentes": _cifras_para_agentes(cifras_raw, moneda_local, ipc_ficha, ipc_serie),
    }
