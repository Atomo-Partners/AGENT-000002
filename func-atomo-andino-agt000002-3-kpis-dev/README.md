# Function 3 · KPIs y scorecard (AGENT-000002 · Fase 2)

Calcula los indicadores del catálogo de Átomo con las cifras de EMIS y los compara contra el scorecard de
la industria. Todo es determinístico: los agentes eligen qué indicadores aplican, la Function decide cuánto
valen y en qué banda caen.

## Rutas

| Ruta | Cuándo corre | Qué hace |
|---|---|---|
| `POST /api/preparar_analisis` | Después del estandarizador, antes del investigador | Industria y métrica ancla desde el Sector priorizado; bloqueo de datos grave; catálogo, anclas y cifras para los agentes |
| `POST /api/calcular_kpis` | Después del selector de KPIs | Serie anual y valor de la serie de cada KPI, en USD |
| `POST /api/armar_scorecard` | Después del cálculo | Banda y bandera de cada KPI contra los cortes del scorecard |

### preparar_analisis
Entrada: `{"sector_priorizado": "...", "pais": "...", "cifras": <cifras_compactas>}`.
Salida: `industria_catalogo {valor, tipo, metrica_ancla_catalogo, justificacion}`, `industrias_catalogo`,
`anclas_por_industria`, `catalogo`, `bloqueo {grave, motivos}`, `calidad`, `movimientos_cambiarios`,
`empleados`, `moneda_calculo: "usd"`, `moneda_local {codigo, origen, advertencia}`, `ipc` (país, medida,
estado) y `cifras_agentes` (cifras_compactas con `moneda_calculo`, `moneda_local` e `ipc_calculo`, la serie
de IPC con que la Function deflacta).

`moneda_local` sale de EMIS (fila de unidades originales) o, si EMIS no la trae (el reporte en español de
Rentek), de la moneda del país del deal en `config/ipc.json`.

Bloqueo grave (el Logic App detiene el flujo): sin ingresos en ningún año; métrica ancla imposible de armar
en ningún año; menos de 2 años con ingresos y activos totales. Todo lo demás sigue con marca.

### calcular_kpis
Entrada: `{"industria", "pais", "metrica_ancla_plan", "cifras": <cifras_compactas>, "kpis": [...selector], "complementarios": [...]}`.
Los campos `catalogo` e `ipc` de la versión anterior se aceptan pero se ignoran: el IPC sale de `config/ipc.json`.

- Alcance: los indicadores generales más los de la industria (de `config/catalogo_kpis.json`).
- Fórmula: la de `config/formulas_kpi.json`, no la del selector. La del selector queda en `formula_selector`.
- Se respeta el `no_aplica` y el `pedir_al_target` del selector. Si el negocio no tiene la cuenta que el
  indicador necesita (sin inventarios, sin costo de ventas), el motor lo marca `no_aplica` solo.
- Complementarios: se calculan con la `formula_calculo` del selector, validada por el motor.

Reglas fijas:
- **USD.** Si la fórmula cruza balance con resultados o flujo, el balance se lleva a la tasa promedio del año
  (`ajuste_tasa_balance: true`). Un saldo del año anterior (`@t-1`) va a la tasa promedio de su propio año.
- **Denominador cero o negativo** (`denominador_positivo` en la fórmula): deuda / EBITDA negativo, ROE con
  patrimonio negativo, etc. El año queda `no_significativo`, la serie no salta a un año anterior y el scorecard
  pone la bandera en rojo.
- **Cuentas opcionales** (`{clave?}`) valen 0 si no existen. Solo cuando la fórmula trae `minimo_uno_de`
  (deuda, gastos de venta y administración) se exige que exista al menos una.
- **Pedir al target** (`pedir_si_falta`): DSCR sin la línea de reembolso de préstamos en EMIS, como los empleados.
- **Base de ingresos:** `{REV}` usa los ingresos netos por ventas (`net_sales_revenue`) y solo si el reporte no
  los trae, el total de ingresos operativos (decisión de Átomo, 2026-11-24).
- **Crecimiento** (`cagr_3_años`): acumulado de la ventana, año final / año inicial - 1, sin anualizar
  (`crecimiento.medida = "acumulado"` en `config/formulas_kpi.json`; con `"compuesto_anual"` vuelve al compuesto). Va
  con `medida_crecimiento`, `nota_moneda` (las cifras ya venían en dólares desde EMIS; el efecto de la tasa de
  cambio no se limpia y se revisa en due diligence) y una advertencia: los rangos de Notion están escritos por año.
  Sobre las cifras en USD, deflactado con el IPC del país de la empresa
  (`config/ipc.json`: Colombia, DANE, variación del índice promedio anual; sin `pais` se usa Colombia). No se convierte
  a pesos: el scorecard se lee en USD. Si el país no tiene IPC configurado, el crecimiento queda nominal con su
  motivo. Mientras el IPC esté `provisional` (sin empalme con el DANE), el indicador lleva una advertencia. Los
  años con movimiento de la tasa promedio mayor al 10 % quedan en `movimientos_cambiarios`, porque el
  crecimiento en USD incluye el efecto del tipo de cambio.
- **Crecimiento real año a año:** además del acumulado de 3 años (que define la banda), el indicador trae
  `crecimiento_real_anual` y `crecimiento_real_ultimo_anio`, con el mismo deflactor. Rentek con ventas netas:
  +32,5 % acumulado 2022-2025, con +18,5 % en 2023, +6,8 % en 2024 y +4,7 % en 2025.
- **Ancla del plan:** si `metrica_ancla_plan` es distinta de la del catálogo, los indicadores que usan `{ANCLA}`
  traen también `valor_ancla_plan` (mismo indicador con la ancla del plan). La banda sigue con la del catálogo.
- **Alertas del selector:** se copian, pero si la Function ya resolvió lo que describen (lo dejó `no_aplica`,
  llevó el balance a tasa promedio o es una decisión de moneda de Átomo) quedan `resuelta: true` con la
  `resolucion`, y no cuentan en `con_alerta_ambiguedad`.
- **Último año con dato** del indicador. Si es anterior al último año del reporte: `anio_desfasado: true`.
- `promedio_3_años`: razón de sumas; si la fórmula usa `@t-1`, promedio simple (`promedio_simple: true`).
- Empleados: solo si el conteo de EMIS está a un año o menos del último año de cifras; si no, `pedir_al_target`.

### armar_scorecard
Entrada: `{"industria", "kpis": <salida de calcular_kpis>, "calidad", "anios_faltantes", "metrica_ancla_usada",
"metrica_ancla_plan"}`. `ancla_distinta` compara la ancla del **plan** (la de la valoración) con la del catálogo;
si `metrica_ancla_plan` no llega, usa la del selector.
Entrada opcional de la industria que confirmó el selector: `industria_tipo` (`exacta|proxy|ninguna`),
`industria_selector`, `industria_sensibilidad`, `industria_evidencia`. Con `proxy`, o si el selector eligió otra
industria, la salida trae `industria_proxy {es_proxy, aviso, ...}`, el aviso al inicio de `motivo_estado` y
`benchmark_proxy: true` en cada fila: los rangos son de la industria más cercana y las banderas son orientativas.
Los cortes salen de `config/scorecard_cortes.json`; ya no se usa el agente de rangos ni la lectura de Notion.
Un valor igual a un corte cae en el rango medio. Salida nueva: `estado_scorecard` (`ok`, `sin_banderas`,
`sin_scorecard`), `alertas_eliminatorias` (cobertura de intereses y deuda neta sobre ancla en rojo o sin dato).

## Configuración (`config/`)

La genera la skill `atomo-config-fase2` (carpeta `skill-atomo-config-fase2`). No se edita a mano salvo
`industrias.json`. `config/fuente/` guarda la foto de Notion con la que se generó y no se publica.

`formulas_kpi.json` trae `estado_revision`: mientras diga `pendiente`, las fórmulas no tienen aprobación
humana. La versión del 2026-11-24 está `aprobado`. Campos de cada fórmula: `no_aplica_si_falta`, `pedir_si_falta`,
`requiere`, `minimo_uno_de`, `denominador_positivo`, `advertir_si_cero` (ver la skill, `reglas_traduccion.md`).

## Pruebas

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Usan `tests/datos/cifras_compactas_rentek.json` y `..._dromos.json`, generadas con el estandarizador 1.2
desde reportes EMIS reales.

## Secretos en Key Vault

| Secreto | URL |
|---|---|
| `func3-preparar-key` (nuevo) | `https://<app>/api/preparar_analisis?code=<clave>` |
| `func3-kpi-key` | `https://<app>/api/calcular_kpis?code=<clave>` |
| `func3-scorecard-key` | `https://<app>/api/armar_scorecard?code=<clave>` |
