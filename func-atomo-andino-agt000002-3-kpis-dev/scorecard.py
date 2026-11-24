"""Scorecard y banderas de AGENT-000002 · Fase 2 (versión 2).

Compara el valor de la serie de cada KPI contra los cortes del scorecard de su industria, que vienen
de config/scorecard_cortes.json (rangos de Notion traducidos por la skill, sin agente).
La banda define la bandera: bajo = roja, medio = amarilla, alto = verde. Un valor igual a un corte
cae en el rango medio, como dicen los textos de Notion ("< 2x", "2x – 4x", "> 4x").
La confianza del benchmark, las alertas de ambigüedad, las fallas de calidad, el desfase de año y los
movimientos cambiarios se muestran como contexto; no cambian la bandera.
"""
from kpi_engine import como_objeto

VERSION_SCORECARD = "2.2"
KPIS_CON_ANCLA = {"margen_metrica_ancla", "conversion_a_caja", "deuda_neta_sobre_ancla"}
BANDERA = {"bajo": "roja", "medio": "amarilla", "alto": "verde"}
# Unidad del catálogo -> unidades de rango compatibles
UNIDADES_COMPATIBLES = {"%": {"%", "pp"}, "x (multiplo)": {"x"}, "ratio": {"x"}, "dias": {"dias"}, "USD": {"usd"}}


def _banda(valor, direccion, inferior, superior):
    if direccion == "Mayor es mejor":
        return "bajo" if valor < inferior else "alto" if valor > superior else "medio"
    if direccion == "Menor es mejor":
        return "bajo" if valor > superior else "alto" if valor < inferior else "medio"
    return "sin_corte"


def _anios_con_falla(calidad, anios_faltantes):
    anios = set()
    for a in como_objeto(anios_faltantes) or []:
        try:
            anios.add(int(str(a)[:4]))
        except ValueError:
            pass
    for anios_regla in ((como_objeto(calidad) or {}).get("fallas") or {}).values():
        for a in anios_regla or []:
            try:
                anios.add(int(str(a)[:4]))
            except ValueError:
                pass
    return anios


def _fila_base(id_, k, sc, fallas, ancla_distinta):
    anios = k.get("anios_serie") or []
    return {
        "indicador_id": id_,
        "nombre": k.get("nombre"),
        "eje": k.get("eje"),
        "unidad": k.get("unidad"),
        "origen": k.get("origen"),
        "serie_regla": k.get("serie_regla"),
        "valor_evaluado": k.get("valor_serie"),
        "anios_usados": anios,
        "anio_ultimo": k.get("anio_ultimo"),
        "tendencia_3": k.get("tendencia_3", "sin_dato"),
        "direccion": (sc or {}).get("direccion"),
        "corte_inferior": (sc or {}).get("corte_inferior"),
        "corte_superior": (sc or {}).get("corte_superior"),
        "rangos_texto": {r: (sc or {}).get(f"rango_{r}") for r in ("bajo", "medio", "alto")},
        "confianza_benchmark": (sc or {}).get("confianza"),
        "fuente_benchmark": (sc or {}).get("fuente_benchmark"),
        "alerta_ambiguedad": k.get("alerta_ambiguedad") or {"hay": False},
        "ventana_incompleta": bool(k.get("ventana_incompleta")),
        "anio_desfasado": bool(k.get("anio_desfasado")),
        "promedio_simple": bool(k.get("promedio_simple")),
        "no_significativo": bool(k.get("no_significativo")),
        "movimientos_cambiarios": k.get("movimientos_cambiarios") or [],
        "falla_calidad": any(int(a) in fallas for a in anios),
        "ancla_distinta": ancla_distinta and id_ in KPIS_CON_ANCLA,
        # Lecturas al lado de la banda, sin cambiarla: el mismo indicador con la ancla del plan y, en el
        # crecimiento, la serie real año a año.
        "valor_ancla_plan": k.get("valor_ancla_plan"),
        "crecimiento_real_anual": k.get("crecimiento_real_anual"),
        "crecimiento_real_ultimo_anio": k.get("crecimiento_real_ultimo_anio"),
        "advertencias": k.get("advertencias") or [],
        "motivo": "",
    }


def _industria_proxy(payload, industria, cfg):
    """Lectura de la industria que confirmó el selector. El sector de Notion se traduce a una industria del
    catálogo, pero eso no dice si la empresa pertenece a ella: el selector lo confirma con la evidencia y, si
    la empresa no corresponde a ninguna industria del catálogo (por ejemplo renting), la marca como proxy."""
    tipo = str(payload.get("industria_tipo") or "").strip().lower() or None
    elegida = cfg.industria_valida(payload.get("industria_selector")) if payload.get("industria_selector") else None
    sensibilidad = payload.get("industria_sensibilidad") or None
    evidencia = payload.get("industria_evidencia") or None
    distinta = bool(elegida and industria and elegida != industria)
    es_proxy = tipo == "proxy" or distinta
    aviso = ""
    if tipo == "proxy" and industria:
        aviso = (f"INDUSTRIA PROXY: la empresa no pertenece a ninguna industria del catálogo. Los rangos son los "
                 f"de '{industria}', la industria más cercana por modelo de negocio, y no fueron hechos para esta "
                 "empresa: las banderas son orientativas y deben leerse con la evidencia.")
        if sensibilidad:
            aviso += f" Contrastar con '{sensibilidad}'."
    if distinta:
        aviso = (aviso + " " if aviso else "") + (
            f"El selector eligió '{elegida}' pero el scorecard usa '{industria}' (sector de Notion o plan): "
            "revisar qué industria corresponde.")
    return {"es_proxy": es_proxy, "tipo": tipo or "sin_dato", "industria_usada": industria,
            "industria_selector": elegida or payload.get("industria_selector"),
            "industria_sensibilidad": sensibilidad, "evidencia": evidencia, "aviso": aviso}


def armar_scorecard(payload, cfg):
    industria = cfg.industria_valida(payload.get("industria"))
    filas_sc = cfg.filas_scorecard(industria) if industria else {}
    kpis = {k.get("indicador_id"): k for k in como_objeto(payload.get("kpis")) or []
            if isinstance(k, dict) and k.get("indicador_id")}
    fallas = _anios_con_falla(payload.get("calidad"), payload.get("anios_faltantes"))

    ancla_scorecard = cfg.metrica_ancla(industria) if industria else None
    ancla_selector = payload.get("metrica_ancla_usada")
    # La ancla que importa es la del plan (la que usa la valoración); la del selector queda de respaldo.
    ancla_plan = payload.get("metrica_ancla_plan")
    ancla_comparada = ancla_plan or ancla_selector
    ancla_distinta = bool(ancla_scorecard and ancla_comparada) and \
        str(ancla_comparada).strip().lower() != str(ancla_scorecard).strip().lower()

    proxy = _industria_proxy(payload, industria, cfg)

    filas = []
    for id_ in list(filas_sc) + [k for k in kpis if k not in filas_sc]:
        sc, k = filas_sc.get(id_), kpis.get(id_) or {}
        fila = _fila_base(id_, k, sc, fallas, ancla_distinta)
        compatibles = UNIDADES_COMPATIBLES.get(k.get("unidad") or "", set())
        if sc is None:
            fila["banda"] = "sin_benchmark"
            fila["motivo"] = "Indicador sin fila en el scorecard de la industria"
        elif sc.get("direccion") == "N/A":
            fila["banda"] = "no_aplica"
            fila["motivo"] = "El scorecard marca el indicador como N/A para esta industria"
        elif not k:
            fila["banda"] = "sin_dato"
            fila["motivo"] = "El indicador no llegó calculado"
        elif k.get("estado") in ("no_aplica", "pedir_al_target"):
            fila["banda"] = "no_aplica" if k["estado"] == "no_aplica" else "sin_dato"
            fila["motivo"] = k.get("motivo") or k["estado"]
            fila["pedir_al_target"] = k["estado"] == "pedir_al_target"
        elif k.get("no_significativo"):
            # Deuda sobre EBITDA negativo, ROE con patrimonio negativo, etc.: peor que cualquier corte.
            fila["banda"] = "bajo"
            fila["no_significativo"] = True
            fila["motivo"] = k.get("motivo_serie") or "Denominador cero o negativo"
        elif k.get("valor_serie") is None:
            fila["banda"] = "sin_dato"
            fila["motivo"] = k.get("motivo_serie") or k.get("motivo") or "Sin valor para la serie"
        elif not sc.get("interpretable"):
            fila["banda"] = "sin_corte"
            fila["motivo"] = f"El rango no se puede traducir a números ({sc.get('motivo')})"
        elif compatibles and sc.get("unidad_rango") not in compatibles:
            fila["banda"] = "sin_corte"
            fila["motivo"] = f"unidad_distinta: el KPI está en {k.get('unidad')} y el rango en {sc.get('unidad_rango')}"
        else:
            fila["banda"] = _banda(k["valor_serie"], sc["direccion"], sc["corte_inferior"], sc["corte_superior"])
        fila["bandera"] = BANDERA.get(fila["banda"])
        fila["benchmark_proxy"] = proxy["es_proxy"] and sc is not None
        filas.append(fila)

    conteo_por_eje, conteo = {}, {"roja": 0, "amarilla": 0, "verde": 0, "sin_bandera": 0}
    for f in filas:
        if f["banda"] == "sin_benchmark":
            continue
        clave = f["bandera"] or "sin_bandera"
        conteo[clave] += 1
        eje = f["eje"] or "sin_eje"
        conteo_por_eje.setdefault(eje, {"roja": 0, "amarilla": 0, "verde": 0, "sin_bandera": 0})[clave] += 1

    if not industria:
        estado, motivo = "sin_scorecard", f"La industria '{payload.get('industria')}' no está en el scorecard"
    elif not any(f["bandera"] for f in filas):
        estado, motivo = "sin_banderas", "Ningún indicador pudo compararse contra el scorecard"
    else:
        estado, motivo = "ok", ""
    if proxy["es_proxy"] and industria:
        motivo = (motivo + " | " if motivo else "") + proxy["aviso"]

    eliminatorios = []
    for f in filas:
        if f["indicador_id"] in cfg.eliminatorios and f["bandera"] in ("roja", None) and f["banda"] != "no_aplica":
            eliminatorios.append({"indicador_id": f["indicador_id"], "banda": f["banda"],
                                  "valor_evaluado": f["valor_evaluado"],
                                  "motivo": "En rojo" if f["bandera"] == "roja" else f"Sin bandera: {f['motivo']}"})

    return {
        "version_scorecard": VERSION_SCORECARD,
        "version_config": cfg.version(),
        "estado_scorecard": estado,
        "motivo_estado": motivo,
        "industria": industria,
        "industria_recibida": payload.get("industria"),
        "industria_proxy": proxy,
        "metrica_ancla_scorecard": ancla_scorecard,
        "metrica_ancla_selector": ancla_selector,
        "metrica_ancla_plan": ancla_plan,
        "ancla_distinta": ancla_distinta,
        "conteo_banderas": conteo,
        "conteo_por_eje": conteo_por_eje,
        "banderas_rojas": [f["indicador_id"] for f in filas if f["bandera"] == "roja"],
        "alertas_eliminatorias": eliminatorios,
        "puntos_ciegos": [{"indicador_id": f["indicador_id"], "banda": f["banda"], "motivo": f["motivo"]}
                          for f in filas if f["bandera"] is None and f["banda"] not in ("sin_benchmark", "no_aplica")],
        "filas": filas,
    }
