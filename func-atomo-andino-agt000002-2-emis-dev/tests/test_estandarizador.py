"""Pruebas del estandarizador. Correr con:  python -m pytest -q"""

import json
import os

import pytest

from emis_estandarizador import ErrorEntrada, Estandarizador, estandarizar

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


# ------------------------------------------------------------- cambios v1.2 (catálogo 1.1)

@pytest.fixture(scope="module")
def rentek():
    return estandarizar(_cargar("cifras_emis_rentek.json"))


def test_rentek_espanol_sin_filas_de_estados_perdidas(rentek):
    """El EMIS real en español dejaba 112 filas sin reconocer (deuda, caja, gastos financieros, flujo)."""
    assert rentek["metadata"]["idioma_reporte"] == "es"
    assert rentek["calidad"]["filas_estados_no_reconocidas"] == 0
    assert rentek["calidad"]["cuentas_clave_faltantes"] == []
    e = rentek["cifras_compactas"]["estados"]
    assert e["finance_expenses"]["2025"] == -6393.76
    assert e["current_loans_and_borrowings"]["2025"] == 23382.77
    assert e["share_premium"]["2025"] == 3284.31
    assert e["purchase_of_ppe"]["2025"] == -25265.05
    assert e["net_cash_flow_operating"]["2023"] == 14393.94


def test_rentek_ceros_de_flujo_sin_detalle(rentek):
    e = rentek["cifras_compactas"]["estados"]
    assert e["purchase_of_ppe"]["2021"] is None      # EMIS trae 0.00 con inversión total de -11.123
    assert e["net_cash_flow_investing"]["2021"] == -11123.10
    # Bloque vacío: efectivo generado por la operación y sus ajustes en 0.00, con flujo operativo de 2.968
    assert e["cash_generated_from_operations"]["2021"] is None
    assert e["adj_depreciation_impairment_ppe"]["2021"] is None
    assert e["cf_net_profit"]["2021"] == 1676.71
    # Un 0.00 suelto con detalle alrededor se respeta
    assert e["repayment_of_borrowings"]["2022"] == 0.0


def test_rentek_bloques_en_parrafos(rentek):
    """El EMIS en español trae moneda, accionistas, filiales y ejecutivos como párrafos, no como tabla."""
    assert rentek["cifras_compactas"]["moneda_original"] == "cop_miles"
    info = rentek["informacion_basica"]
    assert info["accionistas"] == ["Rentek Latinoamerica Inc"]
    assert info["subsidiarias"] == ["Creditek S.A.S"]            # sufijo partido en dos párrafos
    assert info["revisor_fiscal"][0].startswith("Forvis Mazars Audit")
    ejecutivos = rentek["ejecutivos_clave"]
    assert len(ejecutivos) == 16                                   # el bloque cruza un salto de página
    assert all("Copyright" not in e["nombre"] and "EMIS" not in e["nombre"] for e in ejecutivos)
    assert ejecutivos[0] == {"nombre": "Juan Sebastian Maldonado Gutierrez", "cargo": "Representante Legal"}


def test_rentek_ficha_y_descripcion(rentek):
    """Ficha con varios 'etiqueta: valor' pegados en un párrafo; descripción y productos en una tabla de dos
    columnas; ejecutivos en tablas: nada queda como fila no reconocida."""
    e = rentek["empresa"]
    assert e["emis_id"] == "1186850" and e["nit"] == "830034343"
    assert e["forma_legal"] == "S.A.S." and e["estado_operacional"] == "Operacional"
    assert e["fecha_constitucion"] == "1997-08-11"
    assert e["sede"].startswith("Calle 76 # 11 - 35") and not e["sede"].endswith("Mapa")
    assert e["telefono"] == "+57-60-1-646-1200" and e["email"] == "administrativo@rentek.com.co"
    d = rentek["descripcion_negocio"]
    assert d["descripcion"].startswith("CIT Global Vendor Services") and d["descripcion"].endswith("bien.")
    assert d["productos_principales"].startswith("Alquiler de maquinaria pesada")
    assert rentek["informacion_basica"]["capital_registrado"] == "COP 1,013,790,000 (2024)"
    assert rentek["filas_no_reconocidas"] == []


def test_parrafos_con_titulo_partido_y_pares_pegados():
    entrada = {"tablas": [], "parrafos": [
        "Forma Legal: S.A.S. Estatus Operacional: Operacional EMISid: 123 CO-NIT: 900 Fecha de Incorporación: 1 de enero de 2010",
        "Company Tear Sheet -", "Key Executives", "Ana Perez", "Legal Representative", "FISCAL PERIOD"]}
    est = Estandarizador()
    est._reiniciar()
    est._procesar_parrafos(entrada["parrafos"])
    assert est.empresa["nit"] == "900" and est.empresa["fecha_constitucion"] == "2010-01-01"
    assert est.textos["ejecutivos_clave"] == [{"nombre": "Ana Perez", "cargo": "Legal Representative"}]


def test_rentek_tasas_secciones_y_empleados(rentek):
    c = rentek["cifras_compactas"]
    assert c["tasas"]["2023"] == {"resultados": 4325.05, "balance": 3822.05, "flujo": 4325.05}
    assert c["secciones"]["total_assets"] == "balance"
    assert c["secciones"]["operating_profit"] == "resultados"
    assert c["secciones"]["purchase_of_ppe"] == "flujo"
    assert c["empleados"] == {"valor": 65.0, "anio": 2026, "texto": "65 (2026)"}


def test_rentek_patrimonio_con_prima_cuadra(rentek):
    regla = next(v for v in rentek["calidad"]["validaciones"] if v["id"] == "patrimonio")
    assert regla["evaluados"] == 5 and regla["fallas"] == []


def test_cuentas_clave_faltantes_se_reportan(moda_pap):
    assert "deuda_financiera" in moda_pap["calidad"]["cuentas_clave_faltantes"]
    assert any(a["codigo"] == "cuentas_clave_faltantes" for a in moda_pap["calidad"]["advertencias"])
