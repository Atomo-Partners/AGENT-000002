"""Scorecard y banderas de AGENT-000002 · Fase 2 (pasos 25 y 26).

Compara el valor de la serie de cada KPI contra los cortes del scorecard de su
industria. La banda define la bandera: bajo = roja, medio = amarilla, alto = verde.
La confianza del benchmark, las alertas de ambigüedad y las fallas de calidad se
muestran como contexto; no cambian la bandera.
"""
import re

VERSION_SCORECARD = "1.0"
KPIS_CON_ANCLA = {"margen_metrica_ancla", "conversion_a_caja", "deuda_neta_sobre_ancla"}
BANDERA = {"bajo": "roja", "medio": "amarilla", "alto": "verde"}


def _norm(texto):
    t = re.sub(r"\(.*?\)", "", str(texto or "")).lower()
    return re.sub(r"[^a-z0-9]", "", t)


def _banda(valor, direccion, inferior, superior):
    d = _norm(direccion)
    if d in ("na", ""):
        return "no_aplica"
    if valor is None:
        return "sin_dato"
    if inferior is None or superior is None:
        return "sin_corte"
    if d == "mayoresmejor":
        return "bajo" if valor < inferior else "alto" if valor > superior else "medio"
    if d == "menoresmejor":
        return "bajo" if valor > superior else "alto" if valor < inferior else "medio"
    return "sin_corte"


def _anios_con_falla(calidad, anios_faltantes):
    anios = {int(a) for a in (anios_faltantes or [])}
    for anios_regla in ((calidad or {}).get("fallas") or {}).values():
        for a in anios_regla or []:
            try:
                anios.add(int(str(a)[:4]))
            except ValueError:
                pass
    return anios


def armar_scorecard(payload):
    filas_sc = {f.get("indicador_id"): f for f in payload.get("filas_scorecard") or [] if f.get("indicador_id")}
    cortes = {c.get("indicador_id"): c for c in payload.get("cortes") or [] if c.get("indicador_id")}
    kpis = {k.get("indicador_id"): k for k in payload.get("kpis") or [] if k.get("indicador_id")}
    fallas = _anios_con_falla(payload.get("calidad"), payload.get("anios_faltantes"))

    anclas = {_norm(f.get("metrica_ancla")) for f in filas_sc.values() if f.get("metrica_ancla")}
    ancla_scorecard = next((f.get("metrica_ancla") for f in filas_sc.values() if f.get("metrica_ancla")), None)
    ancla_usada = payload.get("metrica_ancla_usada")
    ancla_distinta = bool(anclas) and _norm(ancla_usada) not in anclas

    filas = []
    for id_ in list(filas_sc) + [k for k in kpis if k not in filas_sc]:
        sc, k, c = filas_sc.get(id_), kpis.get(id_) or {}, cortes.get(id_) or {}
        anios = k.get("anios_serie") or []
        fila = {
            "indicador_id": id_,
            "nombre": k.get("nombre"),
            "eje": k.get("eje"),
            "unidad": k.get("unidad"),
            "serie_regla": k.get("serie_regla"),
            "valor_evaluado": k.get("valor_serie"),
            "anios_usados": anios,
            "tendencia_3": k.get("tendencia_3", "sin_dato"),
            "direccion": (sc or {}).get("direccion"),
            "corte_inferior": c.get("corte_inferior"),
            "corte_superior": c.get("corte_superior"),
            "rangos_texto": {r: (sc or {}).get(f"rango_{r}") for r in ("bajo", "medio", "alto")},
            "confianza_benchmark": (sc or {}).get("confianza"),
            "fuente_benchmark": (sc or {}).get("fuente_benchmark"),
            "alerta_ambiguedad": k.get("alerta_ambiguedad") or {"hay": False},
            "ventana_incompleta": bool(k.get("ventana_incompleta")),
            "falla_calidad": any(int(a) in fallas for a in anios),
            "ancla_distinta": ancla_distinta and id_ in KPIS_CON_ANCLA,
            "motivo": "",
        }
        if sc is None:
            fila["banda"] = "sin_benchmark"
            fila["motivo"] = "Indicador sin fila en el scorecard de la industria (complementario)"
        elif not k:
            fila["banda"] = "sin_dato"
            fila["motivo"] = "El indicador no llegó calculado"
        elif k.get("valor_serie") is None and _norm(sc.get("direccion")) not in ("na", ""):
            fila["banda"] = "sin_dato"
            fila["motivo"] = k.get("motivo_serie") or k.get("motivo") or "Sin valor para la serie"
        elif c and c.get("interpretable") is False:
            fila["banda"] = "sin_corte" if _norm(sc.get("direccion")) not in ("na", "") else "no_aplica"
            fila["motivo"] = c.get("motivo") or "El rango no se pudo convertir a números"
        else:
            fila["banda"] = _banda(fila["valor_evaluado"], sc.get("direccion"),
                                   fila["corte_inferior"], fila["corte_superior"])
            if fila["banda"] == "sin_dato":
                fila["motivo"] = k.get("motivo_serie") or k.get("motivo") or "Sin valor para la serie"
            elif fila["banda"] == "sin_corte":
                fila["motivo"] = "Faltan cortes numéricos para este indicador"
        fila["bandera"] = BANDERA.get(fila["banda"])
        filas.append(fila)

    conteo_por_eje, conteo = {}, {"roja": 0, "amarilla": 0, "verde": 0, "sin_bandera": 0}
    for f in filas:
        if f["banda"] == "sin_benchmark":
            continue
        clave = f["bandera"] or "sin_bandera"
        conteo[clave] += 1
        eje = f["eje"] or "sin_eje"
        conteo_por_eje.setdefault(eje, {"roja": 0, "amarilla": 0, "verde": 0, "sin_bandera": 0})[clave] += 1

    return {
        "version_scorecard": VERSION_SCORECARD,
        "industria": payload.get("industria"),
        "metrica_ancla_scorecard": ancla_scorecard,
        "metrica_ancla_usada": ancla_usada,
        "ancla_distinta": ancla_distinta,
        "conteo_banderas": conteo,
        "conteo_por_eje": conteo_por_eje,
        "banderas_rojas": [f["indicador_id"] for f in filas if f["bandera"] == "roja"],
        "puntos_ciegos": [{"indicador_id": f["indicador_id"], "banda": f["banda"], "motivo": f["motivo"]}
                          for f in filas if f["bandera"] is None and f["banda"] != "sin_benchmark"],
        "filas": filas,
    }
