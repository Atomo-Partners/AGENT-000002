"""Pruebas de la Function 3. Correr con:  python -m pytest -q

Datos: cifras_compactas generadas con el estandarizador 1.2 a partir de los reportes EMIS reales de
Rentek (español, 2021-2025) y Dromos (inglés, sin 2022).
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import configuracion  # noqa: E402
from kpi_engine import ErrorFormula, calcular_todo, compilar, expandir  # noqa: E402
from preparar import preparar  # noqa: E402
from scorecard import armar_scorecard  # noqa: E402

DATOS = os.path.join(os.path.dirname(__file__), "datos")
SECTOR_RENTEK = "Maquinaria equipos eléctricos y autopartes"


def _cargar(nombre):
    with open(os.path.join(DATOS, nombre), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def cfg():
    return configuracion.cargar()


@pytest.fixture(scope="module")
def rentek():
    return _cargar("cifras_compactas_rentek.json")


@pytest.fixture(scope="module")
def dromos():
    return _cargar("cifras_compactas_dromos.json")


@pytest.fixture(scope="module")
def kpis_rentek(cfg, rentek):
    salida = calcular_todo({"cifras": rentek, "industria": "Maquinaria y autopartes", "kpis": []}, cfg)
    return salida, {k["indicador_id"]: k for k in salida["kpis"]}


# ------------------------------------------------------------------ configuración
def test_config_completa(cfg):
    assert len(cfg.industrias) == 10
    assert sum(len(i["filas"]) for i in cfg.cortes["industrias"].values()) == 181
    sin_formula = [i["indicador_id"] for i in cfg.catalogo["indicadores"] if i["indicador_id"] not in cfg.formulas["kpis"]]
    assert sin_formula == []
    for id_, d in cfg.formulas["kpis"].items():
        for f in [d["formula"]] + list(d.get("alternativas") or []):
            for candidata in expandir(f, cfg.formulas["macros"], "{EBITDA}"):
                compilar(candidata)  # no lanza


def test_conf_dias_inventario_corregido_a_confecciones(cfg):
    generales = {i["indicador_id"] for i in cfg.kpis_en_alcance("Maquinaria y autopartes") if i["alcance"] == "general"}
    assert "conf_dias_inventario" not in generales
    assert "conf_dias_inventario" in {i["indicador_id"] for i in cfg.kpis_en_alcance("Confecciones y apparel")}


def test_cortes_traducidos(cfg):
    fila = cfg.filas_scorecard("Distribucion y retail especializado")["dist_ingreso_por_empleado"]
    assert (fila["corte_inferior"], fila["corte_superior"], fila["unidad_rango"]) == (100000, 200000, "usd")
    fondeo = cfg.filas_scorecard("Servicios financieros especializados")["fin_costo_fondeo"]
    assert fondeo["interpretable"] is False and fondeo["motivo"] == "depende_de_dato_externo"


# ------------------------------------------------------------------ preparar
def test_preparar_industria_desde_sector(cfg, rentek):
    p = preparar({"sector_priorizado": SECTOR_RENTEK, "cifras": rentek}, cfg)
    assert p["industria_catalogo"]["valor"] == "Maquinaria y autopartes"
    assert p["industria_catalogo"]["metrica_ancla_catalogo"] == "EBITDA"
    assert p["bloqueo"]["grave"] is False
    assert p["cifras_agentes"]["moneda_calculo"] == "usd"
    assert len(p["catalogo"]) == 55


def test_preparar_sector_desconocido(cfg, rentek):
    p = preparar({"sector_priorizado": "Turismo", "cifras": rentek}, cfg)
    assert p["industria_catalogo"]["tipo"] == "ninguna"


def test_bloqueo_grave_sin_ingresos(cfg, rentek):
    sin_ingresos = json.loads(json.dumps(rentek))
    for k in ("total_operating_revenue", "net_sales_revenue"):
        sin_ingresos["estados"].pop(k)
    p = preparar({"sector_priorizado": SECTOR_RENTEK, "cifras": sin_ingresos}, cfg)
    assert p["bloqueo"]["grave"] is True
    assert any("ingresos" in m for m in p["bloqueo"]["motivos"])


# ------------------------------------------------------------------ cálculo
def test_crecimiento_real_usd_deflactado_con_ipc_colombia(kpis_rentek):
    salida, k = kpis_rentek
    r = k["crecimiento_real"]
    # Ventas netas 16.098,71 (2022) -> 26.713,97 (2025) en USD, deflactado con la variación del IPC promedio
    # anual de Colombia 2023-2025 (11,74 %, 6,61 %, 5,14 %; DANE), sin convertir a pesos. Crecimiento
    # acumulado: año final / año inicial - 1, sin anualizar (decisión de Átomo, 2026-11-24).
    esperado = (26713.97 / 16098.71) / (1.1174 * 1.0661 * 1.0514) - 1
    assert r["valor_serie"] == pytest.approx(esperado * 100, abs=1e-3)
    assert r["medida_crecimiento"] == "acumulado"
    assert "due diligence" in r["nota_moneda"]
    assert any("por año" in a for a in r["advertencias"])
    assert r["deflactado"] is True and r["anios_serie"] == [2022, 2025]
    assert r["deflactor"]["pais"] == "Colombia"
    assert r["deflactor"]["estado"] == "empalmado"          # serie de índices del DANE, oct-2026
    assert not any("provisional" in a for a in r["advertencias"])
    assert salida["ipc"]["pais"] == "Colombia"


def test_pais_sin_ipc_no_deflacta(cfg, rentek):
    k = {r["indicador_id"]: r for r in calcular_todo(
        {"cifras": rentek, "industria": "Maquinaria y autopartes", "kpis": [], "pais": "Perú"}, cfg)["kpis"]}
    r = k["crecimiento_real"]
    assert r["deflactado"] is False and "Sin IPC" in r["motivo_serie"]


def test_pais_del_deal_resuelve_moneda_local(cfg, rentek, dromos):
    # Rentek: la Function 2 ya lee 'COP Miles' de los párrafos del EMIS en español.
    p = preparar({"sector_priorizado": SECTOR_RENTEK, "cifras": rentek, "pais": "colombia"}, cfg)
    assert p["moneda_local"]["codigo"] == "COP" and p["moneda_local"]["origen"].startswith("EMIS")
    # Sin la fila de unidades originales, la moneda sale del país del deal.
    sin_moneda = dict(rentek, moneda_original=None)
    q = preparar({"sector_priorizado": SECTOR_RENTEK, "cifras": sin_moneda, "pais": "colombia"}, cfg)
    assert q["moneda_local"]["codigo"] == "COP" and "País del deal" in q["moneda_local"]["origen"]
    assert p["cifras_agentes"]["ipc_calculo"]["por_anio"]["2023"] == 11.74
    assert p["cifras_agentes"]["ipc_calculo"]["medida"] == "variacion_promedio"
    d = preparar({"sector_priorizado": SECTOR_RENTEK, "cifras": dromos, "pais": "Colombia"}, cfg)
    assert d["moneda_local"]["codigo"] == "COP" and d["moneda_local"]["origen"].startswith("EMIS")


def test_alertas_resueltas_por_la_function(cfg, rentek):
    selector = [
        {"indicador_id": "deuda_ebitda", "estado": "calculable",
         "alerta_ambiguedad": {"hay": True, "tipo": "moneda", "descripcion": "tasa de cierre vs promedio"}},
        {"indicador_id": "gasto_sga_sobre_ventas", "estado": "calculable",
         "alerta_ambiguedad": {"hay": True, "tipo": "concepto_aproximado", "descripcion": "solo administración"}},
        {"indicador_id": "patrimonio_sobre_capital_pagado", "estado": "calculable",
         "alerta_ambiguedad": {"hay": True, "tipo": "otro", "descripcion": "acciones propias"}},
    ]
    salida = calcular_todo({"cifras": rentek, "industria": "Maquinaria y autopartes", "kpis": selector}, cfg)
    k = {r["indicador_id"]: r for r in salida["kpis"]}
    assert k["deuda_ebitda"]["alerta_ambiguedad"]["resuelta"] is True
    assert k["gasto_sga_sobre_ventas"]["alerta_ambiguedad"]["resuelta"] is True   # la Function lo dejó no_aplica
    assert k["patrimonio_sobre_capital_pagado"]["alerta_ambiguedad"]["resuelta"] is False
    assert salida["con_alerta_ambiguedad"] == ["patrimonio_sobre_capital_pagado"]


def test_volatilidad_de_crecimiento_con_cuatro_datos_es_ventana_completa(kpis_rentek):
    _, k = kpis_rentek
    r = k["maq_volatilidad_ingresos"]
    assert r["anios_serie"] == [2022, 2023, 2024, 2025] and r["ventana_incompleta"] is False


def test_ancla_del_plan_manda_en_el_scorecard(cfg, kpis_rentek):
    salida, _ = kpis_rentek
    s = armar_scorecard({"industria": "Maquinaria y autopartes", "kpis": salida["kpis"],
                         "metrica_ancla_usada": "EBITDA", "metrica_ancla_plan": "EBIT"}, cfg)
    assert s["ancla_distinta"] is True
    fila = next(f for f in s["filas"] if f["indicador_id"] == "deuda_neta_sobre_ancla")
    assert fila["ancla_distinta"] is True


def test_balance_llevado_a_tasa_promedio(kpis_rentek, rentek):
    _, k = kpis_rentek
    r = k["rotacion_activos"]
    assert r["ajuste_tasa_balance"] is True
    t = rentek["tasas"]["2025"]
    activos_ajustados = rentek["estados"]["total_assets"]["2025"] * t["balance"] / t["resultados"]
    v2025 = next(p["valor"] for p in r["serie"] if p["anio"] == 2025)
    assert v2025 == pytest.approx(rentek["estados"]["net_sales_revenue"]["2025"] / activos_ajustados, abs=1e-4)
    assert k["capex_venta"]["ajuste_tasa_balance"] is False  # flujo / resultados: misma tasa


def test_no_aplica_sin_inventarios_ni_costo(kpis_rentek):
    _, k = kpis_rentek
    assert k["margen_bruto"]["estado"] == "no_aplica"
    assert k["ciclo_conversion_efectivo"]["estado"] == "no_aplica"
    assert k["maq_dias_inventario"]["estado"] == "no_aplica"


def test_cobertura_y_signos(kpis_rentek, rentek):
    _, k = kpis_rentek
    e = rentek["estados"]
    assert k["cobertura_intereses"]["valor_serie"] == pytest.approx(
        e["operating_profit"]["2025"] / -e["finance_expenses"]["2025"], abs=1e-4)
    assert k["capex_venta"]["valor_serie"] > 0


def test_ultimo_anio_con_dato_y_desfase(cfg, rentek):
    sin_2025 = json.loads(json.dumps(rentek))
    sin_2025["estados"]["finance_expenses"]["2025"] = None
    k = {r["indicador_id"]: r for r in calcular_todo({"cifras": sin_2025, "industria": "Maquinaria y autopartes",
                                                       "kpis": []}, cfg)["kpis"]}
    r = k["cobertura_intereses"]
    assert r["anio_ultimo"] == 2024 and r["anio_desfasado"] is True and r["valor_serie"] is not None


def test_selector_no_aplica_se_respeta(cfg, rentek):
    kpis = [{"indicador_id": "roa_operativo", "estado": "no_aplica", "motivo": "prueba"}]
    k = {r["indicador_id"]: r for r in calcular_todo({"cifras": rentek, "industria": "Maquinaria y autopartes",
                                                       "kpis": kpis}, cfg)["kpis"]}
    assert k["roa_operativo"]["estado"] == "no_aplica"


def test_kpis_como_texto_json(cfg, rentek):
    """Logic Apps a veces manda listas como texto."""
    salida = calcular_todo({"cifras": json.dumps(rentek), "industria": "Maquinaria y autopartes",
                            "kpis": json.dumps([])}, cfg)
    assert salida["resumen"]["calculado"] > 0


def test_empleados_desactualizados_piden_al_target(cfg, dromos):
    con_2012 = json.loads(json.dumps(dromos))
    con_2012["empleados"] = {"valor": 65, "anio": 2012, "texto": "65 (2012)"}
    k = {r["indicador_id"]: r for r in calcular_todo({"cifras": con_2012, "industria": "Veterinarias y mascotas",
                                                       "kpis": []}, cfg)["kpis"]}
    assert k["vet_ingreso_por_empleado"]["estado"] == "pedir_al_target"
    assert "2012" in k["vet_ingreso_por_empleado"]["motivo"]


def test_empleados_vigentes_en_usd(cfg, rentek):
    k = {r["indicador_id"]: r for r in calcular_todo({"cifras": rentek, "industria": "Veterinarias y mascotas",
                                                       "kpis": []}, cfg)["kpis"]}
    r = k["vet_ingreso_por_empleado"]
    assert r["valor_serie"] == pytest.approx(rentek["estados"]["net_sales_revenue"]["2025"] * 1000 / 65, abs=1e-2)


def test_dromos_sin_2022_cagr_sin_dato(cfg, dromos):
    k = {r["indicador_id"]: r for r in calcular_todo({"cifras": dromos, "industria": "Construccion liviana y fertilizantes",
                                                       "kpis": []}, cfg)["kpis"]}
    assert k["crecimiento_real"]["valor_serie"] is None
    assert "2022" in k["crecimiento_real"]["motivo_serie"]


def test_complementario_con_formula_del_selector(cfg, rentek):
    comp = [{"indicador_id": "comp_margen_neto", "nombre": "Margen neto", "alcance": "complementario", "eje": "Rentabilidad",
             "unidad": "%", "serie": "promedio_3_años", "estado": "calculable",
             "formula_calculo": "{net_profit} / {total_operating_revenue} * 100"}]
    salida = calcular_todo({"cifras": rentek, "industria": "Maquinaria y autopartes", "kpis": [], "complementarios": comp}, cfg)
    r = next(x for x in salida["kpis"] if x["indicador_id"] == "comp_margen_neto")
    assert r["origen"] == "complementario" and r["estado"] == "calculado"


def test_formula_peligrosa_rechazada():
    with pytest.raises(ErrorFormula):
        compilar("__import__('os').system('x') + {total_assets}")
    with pytest.raises(ErrorFormula):
        compilar("{total_assets} ** 2")


# ------------------------------------------------------------------ scorecard
def test_scorecard_rentek(cfg, rentek, kpis_rentek):
    salida, _ = kpis_rentek
    s = armar_scorecard({"industria": "Maquinaria y autopartes", "kpis": salida["kpis"], "calidad": rentek["calidad"],
                         "anios_faltantes": rentek["anios_faltantes"], "metrica_ancla_usada": "EBITDA"}, cfg)
    assert s["estado_scorecard"] == "ok"
    filas = {f["indicador_id"]: f for f in s["filas"]}
    assert filas["cobertura_intereses"]["bandera"] == "roja"
    assert any(a["indicador_id"] == "cobertura_intereses" for a in s["alertas_eliminatorias"])
    assert filas["margen_bruto"]["banda"] == "no_aplica"
    assert s["ancla_distinta"] is False


def test_scorecard_marca_industria_proxy(cfg, kpis_rentek):
    """Renting no está en el catálogo: el selector usa Maquinaria como proxy y el scorecard lo dice en todo lado."""
    salida, _ = kpis_rentek
    s = armar_scorecard({"industria": "Maquinaria y autopartes", "kpis": salida["kpis"], "industria_tipo": "proxy",
                         "industria_selector": "Maquinaria y autopartes",
                         "industria_sensibilidad": "Servicios financieros especializados",
                         "industria_evidencia": "Alquiler de maquinaria y flotas (renting)"}, cfg)
    p = s["industria_proxy"]
    assert p["es_proxy"] is True and "INDUSTRIA PROXY" in p["aviso"] and "Servicios financieros" in p["aviso"]
    assert "INDUSTRIA PROXY" in s["motivo_estado"]
    assert all(f["benchmark_proxy"] for f in s["filas"] if f["banda"] != "sin_benchmark")
    exacta = armar_scorecard({"industria": "Maquinaria y autopartes", "kpis": salida["kpis"],
                              "industria_tipo": "exacta"}, cfg)
    assert exacta["industria_proxy"]["es_proxy"] is False and exacta["motivo_estado"] == ""
    distinta = armar_scorecard({"industria": "Maquinaria y autopartes", "kpis": salida["kpis"],
                                "industria_tipo": "exacta", "industria_selector": "Servicios financieros especializados"}, cfg)
    assert distinta["industria_proxy"]["es_proxy"] is True and "revisar" in distinta["industria_proxy"]["aviso"]


def test_scorecard_industria_mal_escrita_no_parece_limpio(cfg, kpis_rentek):
    salida, _ = kpis_rentek
    s = armar_scorecard({"industria": "Maquinaria", "kpis": salida["kpis"]}, cfg)
    assert s["estado_scorecard"] == "sin_scorecard"


def test_borde_cae_en_medio(cfg):
    kpi = {"indicador_id": "cobertura_intereses", "unidad": "x (multiplo)", "estado": "calculado", "valor_serie": 2.0}
    s = armar_scorecard({"industria": "Maquinaria y autopartes", "kpis": [kpi]}, cfg)
    fila = next(f for f in s["filas"] if f["indicador_id"] == "cobertura_intereses")
    assert fila["banda"] == "medio"


# ------------------------------------------------------------------ revisión de fórmulas (2026-11-24)
def test_ebitda_negativo_es_rojo_no_verde(cfg, rentek):
    """Deuda / EBITDA negativo daría un múltiplo negativo, que con 'Menor es mejor' saldría verde."""
    c = json.loads(json.dumps(rentek))
    c["estados"]["ebitda"]["2025"] = -1000.0
    salida = calcular_todo({"cifras": c, "industria": "Maquinaria y autopartes", "kpis": []}, cfg)
    k = {r["indicador_id"]: r for r in salida["kpis"]}
    for id_ in ("deuda_ebitda", "deuda_neta_sobre_ancla", "conversion_a_caja"):
        assert k[id_]["no_significativo"] is True, id_
        assert k[id_]["valor_serie"] is None and k[id_]["anio_ultimo"] == 2025  # no salta a 2024
    s = armar_scorecard({"industria": "Maquinaria y autopartes", "kpis": salida["kpis"]}, cfg)
    filas = {f["indicador_id"]: f for f in s["filas"]}
    assert filas["deuda_ebitda"]["bandera"] == "roja"
    assert any(a["indicador_id"] == "deuda_neta_sobre_ancla" for a in s["alertas_eliminatorias"])


def test_opcional_faltante_no_anula_el_indicador(cfg, dromos):
    """Dromos no trae prima de emisión: patrimonio / capital se calcula con el capital suscrito."""
    k = {r["indicador_id"]: r for r in calcular_todo(
        {"cifras": dromos, "industria": "Construccion liviana y fertilizantes", "kpis": []}, cfg)["kpis"]}
    e = dromos["estados"]
    assert k["patrimonio_sobre_capital_pagado"]["valor_serie"] == pytest.approx(
        e["total_equity"]["2025"] / e["issued_capital"]["2025"], abs=1e-3)


def test_sin_ninguna_cuenta_de_deuda_queda_sin_datos(cfg, rentek):
    c = json.loads(json.dumps(rentek))
    for clave in ("current_loans_and_borrowings", "non_current_loans_and_borrowings"):
        del c["estados"][clave]
    k = {r["indicador_id"]: r for r in calcular_todo(
        {"cifras": c, "industria": "Maquinaria y autopartes", "kpis": []}, cfg)["kpis"]}
    assert k["deuda_ebitda"]["estado"] == "sin_datos"


def test_anio_anterior_a_su_propia_tasa(cfg, dromos):
    """Crecimiento de inventarios y de ingresos en la misma moneda: cada saldo a la tasa promedio de su año."""
    salida = calcular_todo({"cifras": dromos, "industria": "Confecciones y apparel", "kpis": []}, cfg)
    r = next(x for x in salida["kpis"] if x["indicador_id"] == "conf_inventario_vs_ventas")
    e, t = dromos["estados"], dromos["tasas"]

    def inv(a):
        return e["inventories"][str(a)] * t[str(a)]["balance"] / t[str(a)]["resultados"]

    rev = e.get("net_sales_revenue") or e["total_operating_revenue"]
    difs = [(inv(a) / inv(a - 1) - rev[str(a)] / rev[str(a - 1)]) * 100 for a in (2024, 2025)]
    assert r["valor_serie"] == pytest.approx(sum(difs) / 2, abs=1e-3)


def test_dscr_sin_reembolsos_se_pide_al_target(cfg, dromos):
    k = {r["indicador_id"]: r for r in calcular_todo(
        {"cifras": dromos, "industria": "Agroindustria y alimentos", "kpis": []}, cfg)["kpis"]}
    assert k["debt_service_coverage_ratio"]["estado"] == "pedir_al_target"


def test_dscr_advierte_reembolso_cero(cfg, rentek):
    k = {r["indicador_id"]: r for r in calcular_todo(
        {"cifras": rentek, "industria": "Agroindustria y alimentos", "kpis": []}, cfg)["kpis"]}
    r = k["debt_service_coverage_ratio"]
    assert r["estado"] == "calculado"
    assert any("repayment_of_borrowings" in a for a in r["advertencias"])


def test_sga_no_aplica_sin_costo_de_ventas(kpis_rentek, cfg, dromos):
    _, k = kpis_rentek
    assert k["gasto_sga_sobre_ventas"]["estado"] == "no_aplica"
    kd = {r["indicador_id"]: r for r in calcular_todo(
        {"cifras": dromos, "industria": "Construccion liviana y fertilizantes", "kpis": []}, cfg)["kpis"]}
    assert kd["gasto_sga_sobre_ventas"]["estado"] == "calculado"
    assert kd["gasto_sga_sobre_ventas"]["valor_serie"] > 0  # gastos negativos vuelven positivos


def test_crecimiento_real_anual_muestra_el_freno(kpis_rentek):
    """El acumulado de 3 años lo arrastra 2023; la serie anual deja ver 2024-2025."""
    _, k = kpis_rentek
    r = k["crecimiento_real"]
    anual = {p["anio"]: p["valor"] for p in r["crecimiento_real_anual"]}
    esperado_2025 = ((26713.97 / 24264.07) / 1.0514 - 1) * 100
    assert anual[2025] == pytest.approx(esperado_2025, abs=1e-3)
    assert r["crecimiento_real_ultimo_anio"] == anual[2025]
    assert anual[2023] > anual[2024] > anual[2025]


def test_indicadores_con_ancla_del_plan(cfg, rentek):
    salida = calcular_todo({"cifras": rentek, "industria": "Maquinaria y autopartes", "kpis": [],
                            "metrica_ancla_plan": "EBIT"}, cfg)
    k = {r["indicador_id"]: r for r in salida["kpis"]}
    r = k["deuda_neta_sobre_ancla"]
    assert r["valor_serie"] == pytest.approx(2.8081, abs=1e-3)                       # EBITDA: banda del catálogo
    assert r["valor_ancla_plan"]["metrica_ancla"] == "EBIT"
    assert r["valor_ancla_plan"]["valor_serie"] == pytest.approx(5.0473, abs=1e-3)   # EBIT: al lado
    assert "valor_ancla_plan" not in k["cobertura_intereses"]                          # no usa el ancla
    s = armar_scorecard({"industria": "Maquinaria y autopartes", "kpis": salida["kpis"],
                         "metrica_ancla_plan": "EBIT"}, cfg)
    fila = next(f for f in s["filas"] if f["indicador_id"] == "deuda_neta_sobre_ancla")
    assert fila["banda"] == "medio" and fila["valor_ancla_plan"]["valor_serie"] == pytest.approx(5.0473, abs=1e-3)


def test_misma_ancla_no_duplica(cfg, rentek):
    salida = calcular_todo({"cifras": rentek, "industria": "Maquinaria y autopartes", "kpis": [],
                            "metrica_ancla_plan": "EBITDA"}, cfg)
    assert salida["metrica_ancla_plan"] is None or salida["metrica_ancla_plan"] == "EBITDA"
    assert all("valor_ancla_plan" not in r for r in salida["kpis"])
