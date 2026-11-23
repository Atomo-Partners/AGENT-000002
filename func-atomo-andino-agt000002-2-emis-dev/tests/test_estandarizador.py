"""Pruebas del estandarizador. Correr con:  python -m pytest -q"""

import json
import os

import pytest

from emis_estandarizador import ErrorEntrada, estandarizar

DATOS = os.path.join(os.path.dirname(__file__), "datos")


def _cargar(nombre):
    with open(os.path.join(DATOS, nombre), encoding="utf-8") as f:
        return json.load(f)


def _fila(filas, id_):
    return next(f for f in filas if f["id"] == id_)


@pytest.fixture(scope="module")
def dromos():
    return estandarizar(_cargar("cifras_emis_dromos.json"))


@pytest.fixture(scope="module")
def moda_pap():
    return estandarizar(_cargar("cifras_emis_moda_pap_simulado.json"))


# ---------------------------------------------------------------- Dromos (inglés, salida real de DI)

def test_dromos_metadata_y_empresa(dromos):
    assert dromos["metadata"]["idioma_reporte"] == "en"
    assert dromos["empresa"]["nit"] == "900215394"
    assert dromos["empresa"]["emis_id"] == "2653351"
    assert dromos["empresa"]["fecha_constitucion"] == "2008-04-22"
    assert "Mosquera" in dromos["empresa"]["sede"]


def test_dromos_estados_completos(dromos):
    ef = dromos["estados_financieros"]
    assert len(ef["estado_resultados"]) == 19
    assert len(ef["balance_general"]) == 41
    assert len(ef["flujo_de_caja"]) == 22
    assert _fila(ef["estado_resultados"], "gross_profit")["valores"]["2025"] == 6892.41
    assert dromos["filas_no_reconocidas"] == []


def test_dromos_provisiones_por_padre(dromos):
    balance = dromos["estados_financieros"]["balance_general"]
    assert _fila(balance, "non_current_provisions")["valores"]["2020"] == 110.06
    assert _fila(balance, "current_provisions")["valores"]["2025"] == 414.5


def test_dromos_tasas_por_estado(dromos):
    p2025 = next(p for p in dromos["estados_financieros"]["periodos"] if p["anio"] == 2025)
    assert p2025["tasa_resultados"] == 4052.71
    assert p2025["tasa_balance"] == 3757.08
    assert p2025["inicio"] == "2025-01-01" and p2025["fin"] == "2025-12-31"


def test_dromos_porcentajes_como_fraccion(dromos):
    margen = _fila(dromos["ratios"]["rentabilidad"], "ebitda_margin")
    assert margen["valores"]["2021"] == pytest.approx(0.1689)
    tendencia = _fila(dromos["ratios"]["tendencias_de_crecimiento"], "net_sales_revenue_trend")
    assert tendencia["valores"]["2023"] is None


def test_dromos_ortiz_reparada(dromos):
    ortiz = next(p for p in dromos["empresas_similares"] if p["nombre"].startswith("Ortiz"))
    assert ortiz["nombre"] == "Ortiz Construcciones y Proyectos S A Sucursal Colombia"
    assert ortiz["roe"] == pytest.approx(0.5379)
    assert ortiz["anio_fiscal"] == 2025
    assert len(dromos["empresas_similares"]) == 10


def test_dromos_calidad(dromos):
    calidad = dromos["calidad"]
    assert calidad["anios_faltantes"] == [2022]
    caja = next(v for v in calidad["validaciones"] if v["id"] == "caja_final")
    assert caja["fallas"] == ["2021"]          # dato dudoso del propio EMIS
    assert calidad["estado"] == "error"
    resumen = next(v for v in calidad["validaciones"] if v["id"] == "resumen_vs_estados")
    assert resumen["fallas"] == []


# --------------------------------------------------- Moda Pap (español, entrada simulada)

def test_moda_pap_idioma_y_empresa(moda_pap):
    assert moda_pap["metadata"]["idioma_reporte"] == "es"
    assert moda_pap["empresa"]["fecha_constitucion"] == "2011-03-25"
    assert moda_pap["empresa"]["nit"] == "900424137"


def test_moda_pap_secciones_ausentes(moda_pap):
    assert "flujo_de_caja" in moda_pap["calidad"]["secciones_ausentes"]
    assert moda_pap["estados_financieros"]["flujo_de_caja"] == []
    assert len(moda_pap["estados_financieros"]["estado_resultados"]) == 12


def test_moda_pap_textos_normalizados(moda_pap):
    detalles = moda_pap["estados_financieros"]["detalles_reporte"]
    assert detalles["statement_type"]["valores"]["2025"] == "anual_individual"
    assert detalles["units"]["valores"]["2025"] == "usd_miles"


# ------------------------------------------------------------------------ entrada inválida

def test_entrada_sin_tablas():
    with pytest.raises(ErrorEntrada):
        estandarizar({"archivo_origen": "x.pdf", "tablas": []})


# ------------------------------------------------------------- cambios v1.1

def test_coma_decimal_se_rechaza():
    from emis_estandarizador.utilidades import parsear_numero
    assert parsear_numero("49,86%") is None
    assert parsear_numero("1.083,42") is None
    assert parsear_numero("1,083.42") == (1083.42, "")
    assert parsear_numero("2,173") == (2173.0, "")


def test_cifras_compactas(dromos):
    c = dromos["cifras_compactas"]
    assert c["estados"]["gross_profit"]["2025"] == 6892.41
    assert c["ratios"]["ebitda_margin"]["2021"] == pytest.approx(0.1689)
    assert c["anios_faltantes"] == [2022]
    assert c["calidad"]["fallas"]["caja_final"] == ["2021"]
