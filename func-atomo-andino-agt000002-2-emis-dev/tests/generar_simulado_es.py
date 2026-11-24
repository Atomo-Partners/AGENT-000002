"""Genera un JSON con la forma de Document Intelligence a partir de las tablas del reporte EMIS de Moda Pap (español).
Sirve solo para probar el idioma y una empresa con menos cuentas; no reemplaza una extracción real."""
import json

def tabla(pagina, filas):
    ncols = max(len(f) for f in filas)
    cells = []
    for r, f in enumerate(filas):
        for c in range(ncols):
            txt = f[c] if c < len(f) else ""
            cells.append({"rowIndex": r, "columnIndex": c, "content": txt, "kind": "content"})
    return {"rowCount": len(filas), "columnCount": ncols, "cells": cells, "boundingRegions": [{"pageNumber": pagina}]}

A5 = ["NOMBRE", "2021", "2022", "2023", "2024", "2025"]
def fila(txt):
    partes = txt.split("|")
    return [p.strip() for p in partes]

tablas = [
 tabla(2, [["Hoja Resumen de Compañía", "3"], ["Información de Contacto", "3"], ["Datos Financieros Claves", "3"], ["Ratios", "8"], ["Limitaciones de Responsabilidad", "11"]]),
 tabla(3, [["Hoja Resumen de Compañía - Información de Contacto", ""], ["Nombre Completo: Moda Pap S.A.S.", "Casa Matriz"], ["", "Carrera 35 15 B 143 Oficina 9201 Medellín; Antioquia; Colombia;"],
           ["Estado: No cotizada", "Mapa"], ["Forma Legal: S.A.S.", "Teléfono: +57-310-410-8229"], ["Estatus Operacional: Operacional", "Correo Electrónico: administrativa@especia.com.co"],
           ["EMISid: 3918565", ""], ["CO-NIT: 900424137", ""], ["Fecha de Incorporación: 25 de marzo de 2011", ""]]),
 tabla(3, [fila(x) for x in """PERÍODO FISCAL|Y-2022 INDIV.|Y-2023 INDIV.|Y-2024 INDIV.|Y-2025 INDIV.
||||
Total Ingreso Operativo|2,173|2,176|1,745|1,436
Ingresos netos por ventas|2,173|2,176|1,745|1,436
Utilidad bruta|1,083|1,259|852|741
Margen De Ganancia Bruta|49.86%|57.88%|48.80%|51.59%
Ganancia operativa (EBIT)|-35|140|35|-47
Margen Operacional|-1.62%|6.45%|1.98%|-3.26%
Ganancia (Pérdida) Neta|-38|92|-37|-47
Margen Neto|-1.73%|4.24%|-2.10%|-3.26%
Activos Totales|390|576|511|689""".split("\n")]),
 tabla(4, [fila(x) for x in """PERÍODO FISCAL|Y-2022 INDIV.|Y-2023 INDIV.|Y-2024 INDIV.|Y-2025 INDIV.
||||
Activos Corrientes|345|539|499|608
Pasivos Totales|259|306|193|535
Deuda Neta|0|0|0|0
Proveedor|Medellin Chamber of Commerce|Medellin Chamber of Commerce|Medellin Chamber of Commerce|Medellin Chamber of Commerce""".split("\n")]),
 tabla(4, [fila(x) for x in """COMPAÑÍA|TOTAL INGRESO OPERATIVO|GANANCIA OPERATIVA (EBIT)|GANANCIA (PÉRDIDA) NETA|RENDIMIENTO SOBRE LOS ACTIVOS (%)|RENDIMIENTO SOBRE EL CAPITAL (%)|UTILIDAD POR ACCIÓN|FECHA DE REFERENCIA DEL PRECIO|AÑO FISCAL
Inversiones Grandes Almacenes Tiendaszayre S.A.S.|781,860|0|141|101.70|N/D|||2022
Manufacturas Eliot S.A.S.|401,738|18,043|8,908|2.10|6.79|||2025
Jakema S.A.S.|296,098|0|0|N/D|N/D|||2025
Crystal S.A.S.|285,031|20,365|9,024|3.50|8.06|||2025
Permoda Ltda|252,581|23,791|1,222|0.32|1.04|||2025
Stf Group S.A.|216,946|13,534|-881|-0.49|-1.60|||2025
H&M Hennes & Mauritz Colombia S A S|191,063|22,457|7,965|6.40|8.80|||2025
Comercializadora Arturo Calle Sas|175,293|19,109|10,733|11.43|20.88|||2025
Compania de Inversiones Textiles de Moda S.A.S. - Texmoda S.A.S|157,445|9,934|-667|-0.76|236.79|||2025""".split("\n")]),
 tabla(5, [["Compania de Cosmeticos Votre Passion S.A.S.", "152,172", "1,121", "-781", "-4.24", "-53.12", "2025"]]),
 tabla(6, [A5] + [fila(x) for x in """Tipo de Estado Financiero|Anual, Indiv.|Anual, Indiv.|Anual, Indiv.|Anual, Indiv.|Anual, Indiv.
Fecha final del período|2021-12-31|2022-12-31|2023-12-31|2024-12-31|2025-12-31
Proveedor|Medellin Chamber of Commerce|Medellin Chamber of Commerce|Medellin Chamber of Commerce|Medellin Chamber of Commerce|Medellin Chamber of Commerce
Unidades|Miles USD|Miles USD|Miles USD|Miles USD|Miles USD""".split("\n")]),
 tabla(6, [["", "", "", "", "", ""], ["Estado de Resultados", "", "", "", "", ""], A5] + [fila(x) for x in """Tipo de Moneda utilizado para la conversión de la moneda (IFRS)|3,743.09|4,255.44|4,325.05|4,071.35|4,052.71
Total Ingreso Operativo|1,621.00|2,173.06|2,176.16|1,745.10|1,435.92
Ingresos netos por ventas|1,621.00|2,173.06|2,176.16|1,745.10|1,435.92
Costo de mercancías vendidas|-703.65|-1,089.64|-916.66|-893.53|-695.15
Utilidad bruta|917.34|1,083.42|1,259.50|851.57|740.77
Gastos de venta y distribución|-851.74|-1,087.75|-1,050.61|-1,015.30|-1,115.30
Ganancia operativa (EBIT)|42.30|-35.23|140.28|34.57|-46.78
Otros resultados no operativos netos|-23.31|-30.90|6.12|127.15|327.76
Otros ingresos|38.58|68.02|26.87|182.29|389.36
Otros gastos|-61.89|-98.92|-20.75|-55.15|-61.61
Ganancias antes de impuestos|26.36|-37.67|92.29|-36.58|-46.78
Ganancias después de impuestos|26.36|-37.67|92.29|-36.58|-46.78
Ganancia (Pérdida) Neta|26.36|-37.67|92.29|-36.58|-46.78""".split("\n")]),
 tabla(6, [A5] + [fila(x) for x in """Tipo de Moneda utilizado para la conversión de la moneda (IFRS)|3,981.16|4,810.20|3,822.05|4,409.15|3,757.08
Activos Totales|540.60|389.71|575.72|511.07|689.36
Activos no corrientes|53.83|44.23|36.97|12.23|81.28
Propiedad, planta y equipo|53.83|44.23|36.97|12.23|81.28
Activos Corrientes|486.77|345.47|538.76|498.84|608.09
Total de patrimonio y pasivos|540.60|389.71|575.72|511.07|689.36
Total de patrimonio|198.75|131.17|269.51|318.48|153.89
Pasivos Totales|341.85|258.54|306.22|192.58|535.47
Pasivos no corrientes|161.09|120.06|74.31|41.06|0.00
Pasivos Corrientes|180.76|138.48|231.91|151.53|535.47""".split("\n")]),
 tabla(7, [A5] + [fila(x) for x in """Tipo de Estado Financiero|Anual, Indiv.|Anual, Indiv.|Anual, Indiv.|Anual, Indiv.|Anual, Indiv.
Fecha inicial del periodo de reporte|2021-01-01|2022-01-01|2023-01-01|2024-01-01|2025-01-01
Fecha final del período|2021-12-31|2022-12-31|2023-12-31|2024-12-31|2025-12-31
Unidades originales como se reportó|COP Miles|COP Miles|COP Miles|COP Miles|COP Miles
Fuente|C.C. Medellin - EEFF|C.C. Medellin - EEFF|C.C. Medellin - EEFF|C.C. Medellin - EEFF|C.C. Medellin - EEFF""".split("\n")]),
 tabla(8, [A5] + [fila(x) for x in """Tipo de Estado Financiero|Anual, Indiv.|Anual, Indiv.|Anual, Indiv.|Anual, Indiv.|Anual, Indiv.
Fecha final del período|2021-12-31|2022-12-31|2023-12-31|2024-12-31|2025-12-31
Proveedor|Medellin Chamber of Commerce|Medellin Chamber of Commerce|Medellin Chamber of Commerce|Medellin Chamber of Commerce|Medellin Chamber of Commerce
Unidades|Miles USD|Miles USD|Miles USD|Miles USD|Miles USD""".split("\n")]),
 tabla(8, [A5] + [fila(x) for x in """Tipo de Moneda utilizado para la conversión de la moneda (IFRS)|3,743.09|4,255.44|4,325.05|4,071.35|4,052.71
Ratios de Rentabilidad|||||
Rendimiento Sobre Los Activos (ROA)|4.58%|-8.55%|18.14%|-6.61%|-7.32%
Rendimiento Sobre Activos Anualizado|4.58%|-8.55%|18.14%|-6.61%|-7.32%
Rendimiento Sobre El Patrimonio (ROE)|12.47%|-25.41%|38.75%|-10.61%|-32.79%
Rendimiento Sobre El Patrimonio Anualizado|12.47%|-25.41%|38.75%|-10.61%|-32.79%
Rendimiento Sobre El Capital Empleado|11.05%|-12.41%|46.17%|8.88%|-32.79%
Margen De Ganancia Bruta|56.59%|49.86%|57.88%|48.80%|51.59%
Margen Operacional|2.61%|-1.62%|6.45%|1.98%|-3.26%
Margen Neto|1.63%|-1.73%|4.24%|-2.10%|-3.26%
ROA Operativo|7.36%|-8.00%|27.57%|6.25%|-7.32%
Ratios de Eficiencia|||||
Rotación Del Activo Corriente|3.13x|5.56x|4.57x|3.23x|2.55x
Rotación Del Activo No Corriente|28.31x|43.46x|66.61x|131.74x|19.06x
Rotación De Activos|2.82x|4.93x|4.28x|3.15x|2.25x
Rotación Del Capital De Trabajo|4.98x|9.29x|8.03x|4.64x|21.33x
Proporción De Exportación|0.00%|0.00%|0.00%|0.00%|0.00%
Cifras de valoración|||||
Efectivo Neto|-363.59|-292.25|-270.60|-208.56|-496.41
Capital De Trabajo|325.47|233.98|271.16|376.13|67.32
Capital Empleado|382.72|283.98|303.83|389.37|142.67
Ratios de Liquidez|||||
Razón De Liquidez|2.69x|2.49x|2.32x|3.29x|1.14x
Modelo Z-Score de Altman|4.63|3.44|5.64|5.75|0.50""".split("\n")]),
 tabla(9, [fila(x) for x in """Pasivos Corrientes / Pasivos No Corrientes|1.12x|1.15x|3.12x|3.69x|
Deuda / Activos|0.00%|0.00%|0.00%|0.00%|0.00%
Relación Deuda/Capital|0.00%|0.00%|0.00%|0.00%|0.00%
Relación Deuda Largo Plazo/Capital Empleado|0.00%|0.00%|0.00%|0.00%|0.00%
Relación Activos/Patrimonio|272.00%|297.11%|213.62%|160.47%|447.95%
Ratios de Tendencia de Crecimiento|||||
Tendencia De Los Ingresos Netos|68.21%|52.41%|1.78%|-24.51%|-18.09%
Tendencia De Los Ingresos Operacionales|68.21%|52.41%|1.78%|-24.51%|-18.09%
Tendencia De La Utilidad Bruta|64.49%|34.27%|18.15%|-36.35%|-13.41%
Tendencia De La Utilidad Operacional|2,251.81%|-194.69%|-504.70%|-76.80%|-234.71%
Tendencia De La Utilidad Neta|305.04%|-262.49%|-348.97%|-137.32%|27.29%
Tendencia Propiedad, Planta Y Equipo Neto (PP&E)|-33.44%|-0.72%|-33.59%|-61.83%|466.23%
Tendencia De Los Activos Totales|27.84%|-12.90%|17.38%|2.41%|14.94%
Tendencia Del Patrimonio Neto|14.25%|-20.26%|63.26%|36.33%|-58.83%
Ratios de rendimiento|||||
Razón Flujo De Caja Operativo/Ingresos|0.00%|0.00%|0.00%|0.00%|0.00%
Razón Flujo De Caja Operativo/Activos|0.00%|0.00%|0.00%|0.00%|0.00%
Razón Flujo De Caja Operativo/Patrimonio|0.00%|0.00%|0.00%|0.00%|0.00%
Razón Flujo De Caja Operativo/Utilidad Operacional|0.00%|0.00%|0.00%|0.00%|0.00%
Proporciones de tamaño común|||||
Relación Efectivo/Total Activos|0.00%|0.00%|0.00%|0.00%|0.00%
Relación Cuentas Por Cobrar/Activos Totales|0.00%|0.00%|0.00%|0.00%|0.00%
Relación Inventario/Total Activos|0.00%|0.00%|0.00%|0.00%|0.00%
Relación Activo fijo/Total activo|9.96%|11.35%|6.42%|2.39%|11.79%
Relación Pasivo Corriente/Pasivo total|52.88%|53.56%|75.73%|78.68%|100.00%
Razón Salarios Y Prestaciones Laborales/ Ventas|0.00%|0.00%|0.00%|0.00%|0.00%
Relación Gastos Administrativos/Ventas|0.00%|0.00%|0.00%|0.00%|0.00%
Relación Depreciación Y Amortización/Ventas|0.00%|0.00%|0.00%|0.00%|0.00%
Relación Intereses Pagados/Ventas|0.00%|0.00%|0.00%|0.00%|0.00%
Relación Impuesto A La Renta/Ventas|0.00%|0.00%|0.00%|0.00%|0.00%
Más Inforciones|||||""".split("\n")] + [A5] + [fila(x) for x in """Todos los ratios calculados por EMIS|||||
Tipo de Estado Financiero|Anual, Indiv.|Anual, Indiv.|Anual, Indiv.|Anual, Indiv.|Anual, Indiv.""".split("\n")]),
 tabla(10, [fila(x) for x in """Fecha inicial del periodo de reporte|2021-01-01|2022-01-01|2023-01-01|2024-01-01|2025-01-01
Fecha final del período|2021-12-31|2022-12-31|2023-12-31|2024-12-31|2025-12-31
Unidades originales como se reportó|COP Miles|COP Miles|COP Miles|COP Miles|COP Miles
Fuente|C.C. Medellin - EEFF|C.C. Medellin - EEFF|C.C. Medellin - EEFF|C.C. Medellin - EEFF|C.C. Medellin - EEFF""".split("\n")]),
]
salida = {"archivo_origen": "ESPECIA_EMIS.pdf", "fecha_extraccion": "simulado", "modelo": "prebuilt-layout", "tablas": tablas}
json.dump(salida, open("tests/datos/cifras_emis_moda_pap_simulado.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("tablas", len(tablas))
