# Variables CENDEU v2 — documento técnico

> Generado desde [`catalogo/variables.yaml`](../catalogo/variables.yaml) con `cendeu documentar`. No editar a mano:
> los cambios van al catálogo. Diccionario en formato plantilla: [`diccionario_variables.csv`](diccionario_variables.csv).

Variables de comportamiento crediticio por CUIT construidas sobre la Central de Deudores del BCRA, con el enfoque del
Feature Store CRC: diseñadas por dominio de riesgo, con dinámica temporal (VAR, TEN, FMX, RSI) y documentadas antes
de medir su poder. Contexto y decisiones: [PLAN.md](../PLAN.md), [estudio del 24DSF](../reportes/estudio_24dsf.md),
[fuentes BCRA y ARCA](../reportes/fuentes_bcra_arca.md).

## Nomenclatura

`CEN_<OPERACION>_<DESCRIPCION>_<VENTANA>`. `VENTANA` = `<n>M`, los últimos *n* meses terminando en la fecha de
observación (`1M` = el mes de observación). Ejemplo: `CEN_TEN_DEUDA_6M` = deuda actual sobre la de hace 6 meses.

## Cómo se construyen

1. **Base limpia** (`cendeu base`): una fila por CUIT × entidad × mes.
   - Período de corte desde el `deudores.txt` mensual y los 23 anteriores desde el 24DSF (el 24DSF omite entidades en su primer mes).
   - Sólo `tipo_id` 11; situación 11 → 1; duplicados CUIT × entidad × mes: peor situación y suma de montos.
   - Entidades absorbidas o recodificadas reemplazadas por su destino en toda la historia
     ([`referencias/entidades_equivalencias.csv`](../referencias/entidades_equivalencias.csv)).
   - CUITs dados de baja reemplazados por su CUIT de reemplazo (padrón ARCA).
   - Montos en miles de pesos constantes del mes base, deflactados con [`referencias/ipc.csv`](../referencias/ipc.csv)
     (IPC INDEC). Cada base guarda en `_meta.json` el hash del IPC usado y si había meses imputados.
2. **Series densas** (`cendeu variables-v2`): para la fecha de observación *t* se arman 13 meses (t-12..t) por CUIT,
   con ausencia = deuda 0, situación 0, 0 entidades. El mes t-12 sólo sirve como mes previo para altas y transiciones.
   Así las diferencias y el RSI nunca saltan meses (problema del Feature Store CRC).
3. **Variables**: cada una es una expresión SQL del catálogo sobre esas series. No hay SQL de variables escrito a mano.

## Población y target


- **`CEN_TGT_SIT3P_6M`**: Llega a situación 3 o peor en alguna entidad en los 6 meses posteriores a la observación. Definición: `max(SIT) en t+1..t+6 >= 3`.
- **Población**: CUITs presentes en el mes de observación con SIT <= 2, tipo_id 11, no fallecidos a la fecha de observación.
- Ausente en el desempeño = sin deuda informada (no es default). Target a 12 meses (CEN_TGT_SIT3P_12M) queda para iteraciones futuras.

## Convenciones

- **grilla**: Todas las series se calculan sobre una grilla densa CUIT × mes: un CUIT que no figura en un mes tiene deuda 0, situación 0 y 0 entidades. Así las diferencias, ratios temporales y RSI no saltan meses (error del Feature Store CRC).
- **montos**: Miles de pesos constantes de un mes base fijo (default 202609), deflactados por IPC INDEC. Los ratios no dependen de la base.
- **entidades**: Se aplica referencias/entidades_equivalencias.csv antes de contar entidades, altas, bajas y principalidad (las fusiones no generan relaciones nuevas falsas).
- **cuits**: Sólo tipo_id = 11. CUITs dados de baja en el padrón se reemplazan por su CUIT de reemplazo.
- **situacion**: Escala 1–5 (la 11 del mensual se trata como 1); 0 = ausente de la central.
- **historia**: El 24DSF cubre 24 meses. Para la primera fecha de observación de modelado (202509) hay exactamente 12 meses de historia, por eso la ventana máxima es 12M.
- **division_por_cero**: Los ratios con denominador 0 son nulos, nunca 0 ni infinito.

## Fuentes

| Código | Fuente |
|---|---|
| `24DSF` | Bianual de la Central de Deudores (24 meses, situación y monto por CUIT × entidad) |
| `MENSUAL` | deudores.txt mensual (24 campos; sólo existe desde 202609, hay que acumularlo) |
| `1DSF` | Fecha desde la cual el deudor está en situación 1 en todas las entidades (sin tope de 24 meses) |
| `MAEENT` | Maestro de entidades del BCRA + referencias/entidades.csv |
| `PADRON` | Padrón de ARCA (foto, corte 20261030) |

## Series mensuales base

| Serie | Contenido |
|---|---|
| `DEUDA` | Deuda total (todas las entidades) |
| `DEUDA_IRR` | Deuda en situación 2 o peor |
| `DEUDA_MORA` | Deuda en situación 3 o peor |
| `SIT` | Peor situación entre entidades (0 = ausente) |
| `SIT_POND` | Situación promedio ponderada por monto (nula si ausente) |
| `ENT` | Cantidad de entidades |
| `ENT_IRR` | Cantidad de entidades con situación 2 o peor |
| `PCT_IRR` | Proporción de la deuda en situación 2 o peor (nula si deuda 0) |
| `HHI` | Concentración de la deuda entre entidades, suma de participaciones al cuadrado |
| `ENT_PRINC` | Entidad con mayor deuda (desempate por menor código) |

## Operaciones

| Código | Significado |
|---|---|
| `MTO` | Monto en el mes |
| `SUM` | Suma en la ventana |
| `AVG` | Promedio en la ventana |
| `MAX` | Máximo en la ventana |
| `MIN` | Mínimo en la ventana |
| `STD` | Desvío estándar en la ventana |
| `CV` | Coeficiente de variación (desvío / promedio) en la ventana |
| `CNT` | Conteo |
| `PCT` | Proporción (0 a 1) |
| `RAT` | Ratio entre dos magnitudes |
| `FLG` | Indicador 0/1 |
| `CAT` | Categoría |
| `MOD` | Moda en la ventana |
| `RCM` | Recencia, meses desde un evento (0 = ocurre en el mes de observación) |
| `VAR` | Variación relativa mes a mes, (x_t - x_{t-1}) / x_{t-1} |
| `TEN` | Tendencia, x_t / x_{t-n+1} (actual contra el inicio de la ventana) |
| `FMX` | Fracción del total, x_t / suma de la ventana |
| `DIF` | Diferencia absoluta, x_t - x_{t-n+1} |
| `PND` | Pendiente de la regresión lineal de x sobre el mes, dividida por el promedio |
| `RSI` | Relative Strength Index, 100 - 100 / (1 + subas / bajas) sobre las variaciones mensuales de la ventana |
| `IDX` | Índice compuesto (HHI, entropía) |
| `VAL` | Valor de un atributo de la persona a la fecha de observación |

## Resumen

149 variables.

| Bloque | Qué mide | Variables | Uso |
|---|---|---|---|
| [B01_MAGNITUD](#b01_magnitud) | Cuánto debe y cuánto de eso es irregular | 19 | modelo: 19 |
| [B02_CALIDAD](#b02_calidad) | Qué tan mala es y fue su situación | 21 | modelo: 21 |
| [B03_TRANSICIONES](#b03_transiciones) | Cómo se mueve entre situaciones (rolls, curas, rachas) | 12 | modelo: 12 |
| [B04_VELOCIDAD](#b04_velocidad) | A qué ritmo cambian deuda, irregularidad y relaciones | 25 | modelo: 25 |
| [B05_MOMENTUM](#b05_momentum) | RSI, fuerza de la tendencia reciente | 12 | modelo: 12 |
| [B06_RELACION_SISTEMA](#b06_relacion_sistema) | Con cuántas y cuáles entidades opera, altas, bajas, principalidad, concentración | 21 | modelo: 21 |
| [B07_MIX_ENTIDADES](#b07_mix_entidades) | Con qué tipo de entidades se endeuda | 16 | modelo: 16 |
| [B08_FOTO_MENSUAL](#b08_foto_mensual) | Campos que sólo trae el mensual (atraso, refinanciación, garante) | 7 | futuro: 7 |
| [B09_PERSONA](#b09_persona) | Atributos de la persona (padrón, 1DSF) | 10 | exclusion: 1, modelo: 8, monitoreo: 1 |
| [B10_ALERTAS](#b10_alertas) | Combinaciones de señales tempranas | 6 | modelo: 6 |

`uso`: **modelo** = candidata al score; **futuro** = sólo existe desde 202609 (mensual), entra cuando haya historia acumulada; **monitoreo** = no entra al score, sirve para medir sesgos; **exclusion** = define la población.

Sentido esperado: relación hipotética con el riesgo (`+` = más valor, más riesgo). Se contrasta en el informe de poder.

<a id="b01_magnitud"></a>
## B01_MAGNITUD — Cuánto debe y cuánto de eso es irregular

| Variable | Descripción | Definición | Nulos | Sentido | Uso |
|---|---|---|---|---|---|
| `CEN_MTO_DEUDA_1M` | Deuda total (todas las entidades) en el mes de observación | DEUDA_t | nunca (0 si no tiene deuda de ese tipo) | + | modelo |
| `CEN_MTO_DEUDA_IRR_1M` | Deuda en situación 2 o peor en el mes de observación | DEUDA_IRR_t | nunca (0 si no tiene deuda de ese tipo) | + | modelo |
| `CEN_MTO_DEUDA_MORA_1M` | Deuda en situación 3 o peor en el mes de observación | DEUDA_MORA_t | nunca (0 si no tiene deuda de ese tipo) | + | modelo |
| `CEN_AVG_DEUDA_3M` | Promedio de la deuda total en los últimos 3 meses | AVG(DEUDA) sobre t-2..t | nunca | + | modelo |
| `CEN_AVG_DEUDA_6M` | Promedio de la deuda total en los últimos 6 meses | AVG(DEUDA) sobre t-5..t | nunca | + | modelo |
| `CEN_AVG_DEUDA_12M` | Promedio de la deuda total en los últimos 12 meses | AVG(DEUDA) sobre t-11..t | nunca | + | modelo |
| `CEN_MAX_DEUDA_3M` | Máximo de la deuda total en los últimos 3 meses | MAX(DEUDA) sobre t-2..t | nunca | + | modelo |
| `CEN_MAX_DEUDA_6M` | Máximo de la deuda total en los últimos 6 meses | MAX(DEUDA) sobre t-5..t | nunca | + | modelo |
| `CEN_MAX_DEUDA_12M` | Máximo de la deuda total en los últimos 12 meses | MAX(DEUDA) sobre t-11..t | nunca | + | modelo |
| `CEN_AVG_DEUDA_IRR_3M` | Promedio de la deuda en situación 2 o peor en los últimos 3 meses | AVG(DEUDA_IRR) sobre t-2..t | nunca | + | modelo |
| `CEN_AVG_DEUDA_IRR_6M` | Promedio de la deuda en situación 2 o peor en los últimos 6 meses | AVG(DEUDA_IRR) sobre t-5..t | nunca | + | modelo |
| `CEN_AVG_DEUDA_IRR_12M` | Promedio de la deuda en situación 2 o peor en los últimos 12 meses | AVG(DEUDA_IRR) sobre t-11..t | nunca | + | modelo |
| `CEN_MAX_DEUDA_IRR_3M` | Máximo de la deuda en situación 2 o peor en los últimos 3 meses | MAX(DEUDA_IRR) sobre t-2..t | nunca | + | modelo |
| `CEN_MAX_DEUDA_IRR_6M` | Máximo de la deuda en situación 2 o peor en los últimos 6 meses | MAX(DEUDA_IRR) sobre t-5..t | nunca | + | modelo |
| `CEN_MAX_DEUDA_IRR_12M` | Máximo de la deuda en situación 2 o peor en los últimos 12 meses | MAX(DEUDA_IRR) sobre t-11..t | nunca | + | modelo |
| `CEN_PCT_DEUDA_IRR_1M` | Proporción de la deuda actual en situación 2 o peor | DEUDA_IRR_t / DEUDA_t | sin deuda en el mes | + | modelo |
| `CEN_AVG_PCT_DEUDA_IRR_3M` | Promedio de la proporción de deuda irregular en los últimos 3 meses | AVG(PCT_IRR) sobre t-2..t, ignorando meses sin deuda | sin deuda en toda la ventana | + | modelo |
| `CEN_AVG_PCT_DEUDA_IRR_6M` | Promedio de la proporción de deuda irregular en los últimos 6 meses | AVG(PCT_IRR) sobre t-5..t, ignorando meses sin deuda | sin deuda en toda la ventana | + | modelo |
| `CEN_AVG_PCT_DEUDA_IRR_12M` | Promedio de la proporción de deuda irregular en los últimos 12 meses | AVG(PCT_IRR) sobre t-11..t, ignorando meses sin deuda | sin deuda en toda la ventana | + | modelo |

<a id="b02_calidad"></a>
## B02_CALIDAD — Qué tan mala es y fue su situación

| Variable | Descripción | Definición | Nulos | Sentido | Uso |
|---|---|---|---|---|---|
| `CEN_MAX_SIT_1M` | Peor situación en los últimos 1 meses | MAX(SIT) sobre t-0..t | nunca | + | modelo |
| `CEN_MAX_SIT_3M` | Peor situación en los últimos 3 meses | MAX(SIT) sobre t-2..t | nunca | + | modelo |
| `CEN_MAX_SIT_6M` | Peor situación en los últimos 6 meses | MAX(SIT) sobre t-5..t | nunca | + | modelo |
| `CEN_MAX_SIT_12M` | Peor situación en los últimos 12 meses | MAX(SIT) sobre t-11..t | nunca | + | modelo |
| `CEN_AVG_SIT_POND_1M` | Situación ponderada por monto, promedio de los últimos 1 meses | AVG(SIT_POND) sobre t-0..t, ignorando meses ausente | ausente en toda la ventana | + | modelo |
| `CEN_AVG_SIT_POND_3M` | Situación ponderada por monto, promedio de los últimos 3 meses | AVG(SIT_POND) sobre t-2..t, ignorando meses ausente | ausente en toda la ventana | + | modelo |
| `CEN_AVG_SIT_POND_6M` | Situación ponderada por monto, promedio de los últimos 6 meses | AVG(SIT_POND) sobre t-5..t, ignorando meses ausente | ausente en toda la ventana | + | modelo |
| `CEN_AVG_SIT_POND_12M` | Situación ponderada por monto, promedio de los últimos 12 meses | AVG(SIT_POND) sobre t-11..t, ignorando meses ausente | ausente en toda la ventana | + | modelo |
| `CEN_CNT_MESES_SIT2P_3M` | Meses con situación 2 o peor en los últimos 3 meses | COUNT(SIT >= 2) sobre t-2..t | nunca | + | modelo |
| `CEN_CNT_MESES_SIT2P_6M` | Meses con situación 2 o peor en los últimos 6 meses | COUNT(SIT >= 2) sobre t-5..t | nunca | + | modelo |
| `CEN_CNT_MESES_SIT2P_12M` | Meses con situación 2 o peor en los últimos 12 meses | COUNT(SIT >= 2) sobre t-11..t | nunca | + | modelo |
| `CEN_CNT_MESES_SIT3P_6M` | Meses con situación 3 o peor en los últimos 6 meses | COUNT(SIT >= 3) sobre t-5..t | nunca | + | modelo |
| `CEN_CNT_MESES_SIT3P_12M` | Meses con situación 3 o peor en los últimos 12 meses | COUNT(SIT >= 3) sobre t-11..t | nunca | + | modelo |
| `CEN_MOD_SIT_12M` | Situación más frecuente en los últimos 12 meses (incluye 0 = ausente) | MODE(SIT) sobre t-11..t; desempate por la peor | nunca | + | modelo |
| `CEN_RCM_SIT2P_12M` | Meses desde la última vez en situación 2 o peor | t - último mes con SIT >= 2 en t-11..t | nunca tuvo situación 2+ en la ventana | - | modelo |
| `CEN_RCM_SIT3P_12M` | Meses desde la última vez en situación 3 o peor | t - último mes con SIT >= 3 en t-11..t | nunca tuvo situación 3+ en la ventana | - | modelo |
| `CEN_RCM_PEOR_SIT_12M` | Meses desde el peor momento (máxima situación) de los últimos 12 meses | t - último mes con SIT = MAX(SIT) en t-11..t | nunca | - | modelo |
| `CEN_FLG_PEOR_MOMENTO_1M` | El mes de observación es el peor de los últimos 12 meses y la situación es 2 o peor | SIT_t = MAX(SIT en t-11..t) AND SIT_t >= 2 | nunca | + | modelo |
| `CEN_CNT_ENT_SIT2P_1M` | Entidades en las que tiene situación 2 o peor en el mes | ENT_IRR_t | nunca | + | modelo |
| `CEN_CNT_ENT_SIT3P_12M` | Entidades distintas en las que tuvo situación 3 o peor en los últimos 12 meses | COUNT DISTINCT entidad con situación >= 3 en t-11..t | nunca | + | modelo |
| `CEN_FLG_PROC_JUDICIAL_12M` | Alguna deuda informada en proceso judicial o revisión (Ley 25.326) en los últimos 12 meses | MAX(flag IN (1, 2)) en t-11..t | nunca | + | modelo |

Notas:
- `CEN_CNT_MESES_SIT3P_6M`: La población del target excluye SIT_t >= 3, pero puede haberla tenido antes.
- `CEN_CNT_MESES_SIT3P_12M`: La población del target excluye SIT_t >= 3, pero puede haberla tenido antes.
- `CEN_FLG_PROC_JUDICIAL_12M`: 460 filas en 24 meses; se mantiene por pedido del estudio pero se espera poder casi nulo.

<a id="b03_transiciones"></a>
## B03_TRANSICIONES — Cómo se mueve entre situaciones (rolls, curas, rachas)

| Variable | Descripción | Definición | Nulos | Sentido | Uso |
|---|---|---|---|---|---|
| `CEN_CNT_EMPEORA_6M` | Meses en que empeoró la situación respecto del mes anterior, últimos 6 meses | COUNT(SIT_m > SIT_{m-1} AND SIT_{m-1} >= 1) para m en t-4..t | nunca | + | modelo |
| `CEN_CNT_EMPEORA_12M` | Meses en que empeoró la situación respecto del mes anterior, últimos 12 meses | COUNT(SIT_m > SIT_{m-1} AND SIT_{m-1} >= 1) para m en t-10..t | nunca | + | modelo |
| `CEN_CNT_MEJORA_6M` | Meses en que mejoró la situación respecto del mes anterior, últimos 6 meses | COUNT(SIT_m < SIT_{m-1} AND SIT_m >= 1) para m en t-4..t | nunca | - | modelo |
| `CEN_CNT_MEJORA_12M` | Meses en que mejoró la situación respecto del mes anterior, últimos 12 meses | COUNT(SIT_m < SIT_{m-1} AND SIT_m >= 1) para m en t-10..t | nunca | - | modelo |
| `CEN_RAT_CURA_12M` | Tasa de cura individual, salidas de situación 2+ a situación 1 sobre meses en situación 2+ | COUNT(SIT_{m-1} >= 2 AND SIT_m = 1) / COUNT(SIT_{m-1} >= 2) en t-11..t | no estuvo en situación 2+ en la ventana | - | modelo |
| `CEN_RAT_ROLL_2A3_12M` | Tasa de roll individual de situación 2 a 3 o peor | COUNT(SIT_{m-1} = 2 AND SIT_m >= 3) / COUNT(SIT_{m-1} = 2) en t-11..t | no estuvo en situación 2 en la ventana | + | modelo |
| `CEN_MAX_RACHA_SIT2P_12M` | Racha más larga de meses consecutivos en situación 2 o peor | máxima longitud de corrida SIT >= 2 en t-11..t | nunca | + | modelo |
| `CEN_CNT_RACHA_SIT2P_1M` | Meses consecutivos en situación 2 o peor hasta el mes de observación | longitud de la corrida SIT >= 2 que termina en t (0 si SIT_t < 2) | nunca | + | modelo |
| `CEN_CNT_RACHA_SIT1_1M` | Meses consecutivos en situación 1 hasta el mes de observación (tope 12) | longitud de la corrida SIT = 1 que termina en t, contando ausencias intermedias como neutras | nunca | - | modelo |
| `CEN_MAX_SALTO_SIT_12M` | Mayor empeoramiento de situación entre dos meses consecutivos | MAX(SIT_m - SIT_{m-1}) con SIT_{m-1} >= 1, en t-11..t (0 si nunca empeoró) | nunca | + | modelo |
| `CEN_FLG_ENTRADA_SIT4P_12M` | Alguna relación nueva con una entidad que aparece directamente en situación 4 o peor | existe (entidad, m) en t-11..t con situación >= 4 y sin presencia de esa entidad en m-1 | nunca | + | modelo |
| `CEN_CNT_ENT_EMPEORA_3M` | Entidades en las que la situación actual es peor que hace 3 meses | COUNT entidades con situación_t > situación_{t-2} (presentes en ambos meses) | nunca | + | modelo |

Notas:
- `CEN_CNT_EMPEORA_6M`: No cuenta las altas (0 -> k).
- `CEN_CNT_EMPEORA_12M`: No cuenta las altas (0 -> k).
- `CEN_RAT_ROLL_2A3_12M`: Roll de 2 a 3 del sistema = 34% mensual (estudio, sección 4).
- `CEN_CNT_RACHA_SIT1_1M`: Versión sin tope en CEN_CNT_MESES_SIT1_ININT_1M (bloque persona, desde 1DSF).
- `CEN_FLG_ENTRADA_SIT4P_12M`: Proxy de cartera cedida o comprada (estudio, hallazgo 6; fideicomisos de recupero).

<a id="b04_velocidad"></a>
## B04_VELOCIDAD — A qué ritmo cambian deuda, irregularidad y relaciones

| Variable | Descripción | Definición | Nulos | Sentido | Uso |
|---|---|---|---|---|---|
| `CEN_VAR_DEUDA_1M` | Variación relativa de la deuda total respecto del mes anterior | (DEUDA_t - DEUDA_{t-1}) / DEUDA_{t-1} | sin deuda el mes anterior | + | modelo |
| `CEN_AVG_VAR_DEUDA_6M` | Promedio de las variaciones mensuales de la deuda en los últimos 6 meses | AVG(VAR(DEUDA)) sobre t-4..t, ignorando meses con deuda previa 0 | sin variaciones calculables | + | modelo |
| `CEN_AVG_VAR_DEUDA_12M` | Promedio de las variaciones mensuales de la deuda en los últimos 12 meses | AVG(VAR(DEUDA)) sobre t-10..t, ignorando meses con deuda previa 0 | sin variaciones calculables | + | modelo |
| `CEN_TEN_DEUDA_3M` | Deuda total (todas las entidades): valor actual sobre el de hace 3 meses | DEUDA_t / DEUDA_(t-2) | valor 0 al inicio de la ventana | + | modelo |
| `CEN_TEN_DEUDA_6M` | Deuda total (todas las entidades): valor actual sobre el de hace 6 meses | DEUDA_t / DEUDA_(t-5) | valor 0 al inicio de la ventana | + | modelo |
| `CEN_TEN_DEUDA_12M` | Deuda total (todas las entidades): valor actual sobre el de hace 12 meses | DEUDA_t / DEUDA_(t-11) | valor 0 al inicio de la ventana | + | modelo |
| `CEN_TEN_DEUDA_IRR_3M` | Deuda en situación 2 o peor: valor actual sobre el de hace 3 meses | DEUDA_IRR_t / DEUDA_IRR_(t-2) | valor 0 al inicio de la ventana | + | modelo |
| `CEN_TEN_DEUDA_IRR_6M` | Deuda en situación 2 o peor: valor actual sobre el de hace 6 meses | DEUDA_IRR_t / DEUDA_IRR_(t-5) | valor 0 al inicio de la ventana | + | modelo |
| `CEN_TEN_DEUDA_IRR_12M` | Deuda en situación 2 o peor: valor actual sobre el de hace 12 meses | DEUDA_IRR_t / DEUDA_IRR_(t-11) | valor 0 al inicio de la ventana | + | modelo |
| `CEN_TEN_ENT_3M` | Cantidad de entidades: valor actual sobre el de hace 3 meses | ENT_t / ENT_(t-2) | valor 0 al inicio de la ventana | + | modelo |
| `CEN_TEN_ENT_6M` | Cantidad de entidades: valor actual sobre el de hace 6 meses | ENT_t / ENT_(t-5) | valor 0 al inicio de la ventana | + | modelo |
| `CEN_TEN_ENT_12M` | Cantidad de entidades: valor actual sobre el de hace 12 meses | ENT_t / ENT_(t-11) | valor 0 al inicio de la ventana | + | modelo |
| `CEN_FMX_DEUDA_3M` | Deuda total (todas las entidades): peso del mes actual en el total de los últimos 3 meses | DEUDA_t / SUM(DEUDA) sobre t-2..t | suma 0 en la ventana | + | modelo |
| `CEN_FMX_DEUDA_6M` | Deuda total (todas las entidades): peso del mes actual en el total de los últimos 6 meses | DEUDA_t / SUM(DEUDA) sobre t-5..t | suma 0 en la ventana | + | modelo |
| `CEN_FMX_DEUDA_12M` | Deuda total (todas las entidades): peso del mes actual en el total de los últimos 12 meses | DEUDA_t / SUM(DEUDA) sobre t-11..t | suma 0 en la ventana | + | modelo |
| `CEN_FMX_ENT_3M` | Cantidad de entidades: peso del mes actual en el total de los últimos 3 meses | ENT_t / SUM(ENT) sobre t-2..t | suma 0 en la ventana | + | modelo |
| `CEN_FMX_ENT_6M` | Cantidad de entidades: peso del mes actual en el total de los últimos 6 meses | ENT_t / SUM(ENT) sobre t-5..t | suma 0 en la ventana | + | modelo |
| `CEN_FMX_ENT_12M` | Cantidad de entidades: peso del mes actual en el total de los últimos 12 meses | ENT_t / SUM(ENT) sobre t-11..t | suma 0 en la ventana | + | modelo |
| `CEN_DIF_SIT_3M` | Cambio de la peor situación respecto de hace 3 meses | SIT_t - SIT_(t-2) | nunca | + | modelo |
| `CEN_DIF_SIT_6M` | Cambio de la peor situación respecto de hace 6 meses | SIT_t - SIT_(t-5) | nunca | + | modelo |
| `CEN_DIF_SIT_12M` | Cambio de la peor situación respecto de hace 12 meses | SIT_t - SIT_(t-11) | nunca | + | modelo |
| `CEN_CV_DEUDA_6M` | Coeficiente de variación de la deuda en los últimos 6 meses | STD(DEUDA) / AVG(DEUDA) sobre t-5..t | promedio 0 | + | modelo |
| `CEN_CV_DEUDA_12M` | Coeficiente de variación de la deuda en los últimos 12 meses | STD(DEUDA) / AVG(DEUDA) sobre t-11..t | promedio 0 | + | modelo |
| `CEN_PND_DEUDA_6M` | Pendiente relativa de la deuda en los últimos 6 meses (crecimiento mensual promedio) | REGR_SLOPE(DEUDA, m) / AVG(DEUDA) sobre t-5..t | promedio 0 | + | modelo |
| `CEN_PND_DEUDA_12M` | Pendiente relativa de la deuda en los últimos 12 meses (crecimiento mensual promedio) | REGR_SLOPE(DEUDA, m) / AVG(DEUDA) sobre t-11..t | promedio 0 | + | modelo |

<a id="b05_momentum"></a>
## B05_MOMENTUM — RSI, fuerza de la tendencia reciente

| Variable | Descripción | Definición | Nulos | Sentido | Uso |
|---|---|---|---|---|---|
| `CEN_RSI_DEUDA_3M` | Deuda total (todas las entidades): RSI de los últimos 3 meses | 100 - 100 / (1 + SUM(subas) / SUM(bajas)) de las diferencias mensuales de DEUDA en t-2..t; 100 si sólo hay subas | sin variaciones en la ventana (serie constante) | + | modelo |
| `CEN_RSI_DEUDA_6M` | Deuda total (todas las entidades): RSI de los últimos 6 meses | 100 - 100 / (1 + SUM(subas) / SUM(bajas)) de las diferencias mensuales de DEUDA en t-5..t; 100 si sólo hay subas | sin variaciones en la ventana (serie constante) | + | modelo |
| `CEN_RSI_DEUDA_12M` | Deuda total (todas las entidades): RSI de los últimos 12 meses | 100 - 100 / (1 + SUM(subas) / SUM(bajas)) de las diferencias mensuales de DEUDA en t-11..t; 100 si sólo hay subas | sin variaciones en la ventana (serie constante) | + | modelo |
| `CEN_RSI_DEUDA_IRR_3M` | Deuda en situación 2 o peor: RSI de los últimos 3 meses | 100 - 100 / (1 + SUM(subas) / SUM(bajas)) de las diferencias mensuales de DEUDA_IRR en t-2..t; 100 si sólo hay subas | sin variaciones en la ventana (serie constante) | + | modelo |
| `CEN_RSI_DEUDA_IRR_6M` | Deuda en situación 2 o peor: RSI de los últimos 6 meses | 100 - 100 / (1 + SUM(subas) / SUM(bajas)) de las diferencias mensuales de DEUDA_IRR en t-5..t; 100 si sólo hay subas | sin variaciones en la ventana (serie constante) | + | modelo |
| `CEN_RSI_DEUDA_IRR_12M` | Deuda en situación 2 o peor: RSI de los últimos 12 meses | 100 - 100 / (1 + SUM(subas) / SUM(bajas)) de las diferencias mensuales de DEUDA_IRR en t-11..t; 100 si sólo hay subas | sin variaciones en la ventana (serie constante) | + | modelo |
| `CEN_RSI_ENT_3M` | Cantidad de entidades: RSI de los últimos 3 meses | 100 - 100 / (1 + SUM(subas) / SUM(bajas)) de las diferencias mensuales de ENT en t-2..t; 100 si sólo hay subas | sin variaciones en la ventana (serie constante) | + | modelo |
| `CEN_RSI_ENT_6M` | Cantidad de entidades: RSI de los últimos 6 meses | 100 - 100 / (1 + SUM(subas) / SUM(bajas)) de las diferencias mensuales de ENT en t-5..t; 100 si sólo hay subas | sin variaciones en la ventana (serie constante) | + | modelo |
| `CEN_RSI_ENT_12M` | Cantidad de entidades: RSI de los últimos 12 meses | 100 - 100 / (1 + SUM(subas) / SUM(bajas)) de las diferencias mensuales de ENT en t-11..t; 100 si sólo hay subas | sin variaciones en la ventana (serie constante) | + | modelo |
| `CEN_RSI_SIT_3M` | Peor situación entre entidades (0 = ausente): RSI de los últimos 3 meses | 100 - 100 / (1 + SUM(subas) / SUM(bajas)) de las diferencias mensuales de SIT en t-2..t; 100 si sólo hay subas | sin variaciones en la ventana (serie constante) | + | modelo |
| `CEN_RSI_SIT_6M` | Peor situación entre entidades (0 = ausente): RSI de los últimos 6 meses | 100 - 100 / (1 + SUM(subas) / SUM(bajas)) de las diferencias mensuales de SIT en t-5..t; 100 si sólo hay subas | sin variaciones en la ventana (serie constante) | + | modelo |
| `CEN_RSI_SIT_12M` | Peor situación entre entidades (0 = ausente): RSI de los últimos 12 meses | 100 - 100 / (1 + SUM(subas) / SUM(bajas)) de las diferencias mensuales de SIT en t-11..t; 100 si sólo hay subas | sin variaciones en la ventana (serie constante) | + | modelo |

Notas:
- `CEN_RSI_DEUDA_3M`: Sobre la grilla densa (en el CRC se calculaba con huecos).
- `CEN_RSI_DEUDA_6M`: Sobre la grilla densa (en el CRC se calculaba con huecos).
- `CEN_RSI_DEUDA_12M`: Sobre la grilla densa (en el CRC se calculaba con huecos).
- `CEN_RSI_DEUDA_IRR_3M`: Sobre la grilla densa (en el CRC se calculaba con huecos).
- `CEN_RSI_DEUDA_IRR_6M`: Sobre la grilla densa (en el CRC se calculaba con huecos).
- `CEN_RSI_DEUDA_IRR_12M`: Sobre la grilla densa (en el CRC se calculaba con huecos).
- `CEN_RSI_ENT_3M`: Sobre la grilla densa (en el CRC se calculaba con huecos).
- `CEN_RSI_ENT_6M`: Sobre la grilla densa (en el CRC se calculaba con huecos).
- `CEN_RSI_ENT_12M`: Sobre la grilla densa (en el CRC se calculaba con huecos).
- `CEN_RSI_SIT_3M`: Sobre la grilla densa (en el CRC se calculaba con huecos).
- `CEN_RSI_SIT_6M`: Sobre la grilla densa (en el CRC se calculaba con huecos).
- `CEN_RSI_SIT_12M`: Sobre la grilla densa (en el CRC se calculaba con huecos).

<a id="b06_relacion_sistema"></a>
## B06_RELACION_SISTEMA — Con cuántas y cuáles entidades opera, altas, bajas, principalidad, concentración

| Variable | Descripción | Definición | Nulos | Sentido | Uso |
|---|---|---|---|---|---|
| `CEN_CNT_ENT_1M` | Entidades con las que tiene deuda en el mes | ENT_t | nunca | + | modelo |
| `CEN_AVG_ENT_6M` | Promedio de la cantidad de entidades en los últimos 6 meses | AVG(ENT) sobre t-5..t | nunca | + | modelo |
| `CEN_AVG_ENT_12M` | Promedio de la cantidad de entidades en los últimos 12 meses | AVG(ENT) sobre t-11..t | nunca | + | modelo |
| `CEN_MAX_ENT_6M` | Máximo de la cantidad de entidades en los últimos 6 meses | MAX(ENT) sobre t-5..t | nunca | + | modelo |
| `CEN_MAX_ENT_12M` | Máximo de la cantidad de entidades en los últimos 12 meses | MAX(ENT) sobre t-11..t | nunca | + | modelo |
| `CEN_CNT_ENT_DISTINTAS_12M` | Entidades distintas con las que tuvo deuda en los últimos 12 meses | COUNT DISTINCT entidad en t-11..t | nunca | + | modelo |
| `CEN_CNT_ALTAS_3M` | Relaciones nuevas con entidades en los últimos 3 meses | COUNT (entidad, m) en t-2..t con deuda en m y sin deuda en esa entidad en m-1 | nunca | + | modelo |
| `CEN_CNT_ALTAS_6M` | Relaciones nuevas con entidades en los últimos 6 meses | COUNT (entidad, m) en t-5..t con deuda en m y sin deuda en esa entidad en m-1 | nunca | + | modelo |
| `CEN_CNT_ALTAS_12M` | Relaciones nuevas con entidades en los últimos 12 meses | COUNT (entidad, m) en t-11..t con deuda en m y sin deuda en esa entidad en m-1 | nunca | + | modelo |
| `CEN_CNT_BAJAS_3M` | Relaciones con entidades que se cerraron en los últimos 3 meses | COUNT (entidad, m) en t-2..t sin deuda en m y con deuda en esa entidad en m-1 | nunca | - | modelo |
| `CEN_CNT_BAJAS_6M` | Relaciones con entidades que se cerraron en los últimos 6 meses | COUNT (entidad, m) en t-5..t sin deuda en m y con deuda en esa entidad en m-1 | nunca | - | modelo |
| `CEN_CNT_BAJAS_12M` | Relaciones con entidades que se cerraron en los últimos 12 meses | COUNT (entidad, m) en t-11..t sin deuda en m y con deuda en esa entidad en m-1 | nunca | - | modelo |
| `CEN_RCM_ULTIMA_ALTA_12M` | Meses desde la última relación nueva | t - último mes con un alta en t-11..t | sin altas en la ventana | - | modelo |
| `CEN_RAT_PRINCIPALIDAD_1M` | Peso de la entidad principal en la deuda total | deuda en ENT_PRINC_t / DEUDA_t | sin deuda en el mes | - | modelo |
| `CEN_CNT_CAMBIO_PRINC_6M` | Veces que cambió la entidad principal en los últimos 6 meses | COUNT(ENT_PRINC_m <> ENT_PRINC_{m-1}) con deuda en ambos meses, m en t-4..t | nunca | + | modelo |
| `CEN_CNT_CAMBIO_PRINC_12M` | Veces que cambió la entidad principal en los últimos 12 meses | COUNT(ENT_PRINC_m <> ENT_PRINC_{m-1}) con deuda en ambos meses, m en t-10..t | nunca | + | modelo |
| `CEN_IDX_HHI_1M` | Concentración de la deuda entre entidades (1 = una sola entidad) | HHI_t = SUM((deuda_entidad / DEUDA_t)^2) | sin deuda en el mes | - | modelo |
| `CEN_CNT_MESES_PRESENTE_12M` | Meses con deuda informada en los últimos 12 meses | COUNT(SIT >= 1) en t-11..t | nunca | - | modelo |
| `CEN_CNT_HUECOS_12M` | Meses ausente entre la primera y la última aparición de los últimos 12 meses | (último mes presente - primer mes presente + 1) - CNT_MESES_PRESENTE en t-11..t | nunca | + | modelo |
| `CEN_RCM_PRIMERA_APARICION_12M` | Antigüedad en la central dentro de la ventana, meses desde la primera aparición | t - primer mes con SIT >= 1 en t-11..t | nunca (la población está presente en t) | - | modelo |
| `CEN_FLG_REACTIVACION_12M` | Volvió a la central después de 3 o más meses ausente | existe m en t-11..t con SIT_m >= 1 y SIT = 0 en m-3..m-1 y presencia antes de m-3 | nunca | + | modelo |

Notas:
- `CEN_CNT_ALTAS_3M`: Requiere un mes previo a la ventana; con equivalencias aplicadas.
- `CEN_CNT_ALTAS_6M`: Requiere un mes previo a la ventana; con equivalencias aplicadas.
- `CEN_CNT_ALTAS_12M`: Requiere un mes previo a la ventana; con equivalencias aplicadas.
- `CEN_CNT_HUECOS_12M`: El 21% de los CUITs tiene huecos (estudio, hallazgo 4).
- `CEN_RCM_PRIMERA_APARICION_12M`: Truncada a 12 meses por la historia disponible; complementa CEN_CNT_MESES_SIT1_ININT_1M.

<a id="b07_mix_entidades"></a>
## B07_MIX_ENTIDADES — Con qué tipo de entidades se endeuda

| Variable | Descripción | Definición | Nulos | Sentido | Uso |
|---|---|---|---|---|---|
| `CEN_PCT_DEUDA_BANCO_1M` | Proporción de la deuda con bancos | deuda con tipo_agrupado BANCO / DEUDA_t | sin deuda en el mes | según tipo: bancos -, PNFC y fideicomisos + | modelo |
| `CEN_PCT_DEUDA_CIA_FIN_1M` | Proporción de la deuda con compañías financieras | deuda con tipo_agrupado CIA_FIN / DEUDA_t | sin deuda en el mes | según tipo: bancos -, PNFC y fideicomisos + | modelo |
| `CEN_PCT_DEUDA_TARJETA_NB_1M` | Proporción de la deuda con emisoras no bancarias de tarjetas | deuda con tipo_agrupado TARJETA_NB / DEUDA_t | sin deuda en el mes | según tipo: bancos -, PNFC y fideicomisos + | modelo |
| `CEN_PCT_DEUDA_PNFC_1M` | Proporción de la deuda con proveedores no financieros de crédito | deuda con tipo_agrupado PNFC / DEUDA_t | sin deuda en el mes | según tipo: bancos -, PNFC y fideicomisos + | modelo |
| `CEN_PCT_DEUDA_FIDEICOMISO_1M` | Proporción de la deuda con fideicomisos financieros | deuda con tipo_agrupado FIDEICOMISO / DEUDA_t | sin deuda en el mes | según tipo: bancos -, PNFC y fideicomisos + | modelo |
| `CEN_CNT_ENT_BANCO_1M` | Cantidad de bancos con las que tiene deuda | COUNT DISTINCT entidad con tipo_agrupado BANCO en t | nunca | según tipo | modelo |
| `CEN_CNT_ENT_TARJETA_NB_1M` | Cantidad de emisoras no bancarias de tarjetas con las que tiene deuda | COUNT DISTINCT entidad con tipo_agrupado TARJETA_NB en t | nunca | según tipo | modelo |
| `CEN_CNT_ENT_PNFC_1M` | Cantidad de proveedores no financieros de crédito con las que tiene deuda | COUNT DISTINCT entidad con tipo_agrupado PNFC en t | nunca | según tipo | modelo |
| `CEN_CNT_TIPOS_ENT_1M` | Cantidad de tipos de entidad distintos con los que tiene deuda | COUNT DISTINCT tipo_agrupado en t | nunca | + | modelo |
| `CEN_IDX_ENTROPIA_TIPO_1M` | Entropía de Shannon de la deuda por tipo de entidad (promedio de 1 meses) | AVG(-SUM(p_tipo * ln p_tipo)) sobre t-0..t, p_tipo = participación de la deuda por tipo_agrupado | sin deuda en toda la ventana | + | modelo |
| `CEN_IDX_ENTROPIA_TIPO_6M` | Entropía de Shannon de la deuda por tipo de entidad (promedio de 6 meses) | AVG(-SUM(p_tipo * ln p_tipo)) sobre t-5..t, p_tipo = participación de la deuda por tipo_agrupado | sin deuda en toda la ventana | + | modelo |
| `CEN_FLG_SOLO_NO_BANCARIA_1M` | Toda su deuda es con entidades no bancarias (ni bancos ni compañías financieras) | DEUDA_t > 0 AND deuda con BANCO y CIA_FIN = 0 | nunca | + | modelo |
| `CEN_MAX_SIT_BANCARIA_12M` | Peor situación con entidades del grupo BANCARIA en los últimos 12 meses | MAX(situación) con entidades BANCO o CIA_FIN (BANCARIA) / resto (NO_BANCARIA) en t-11..t; 0 si no tuvo | nunca | + | modelo |
| `CEN_MAX_SIT_NO_BANCARIA_12M` | Peor situación con entidades del grupo NO_BANCARIA en los últimos 12 meses | MAX(situación) con entidades BANCO o CIA_FIN (BANCARIA) / resto (NO_BANCARIA) en t-11..t; 0 si no tuvo | nunca | + | modelo |
| `CEN_TEN_DEUDA_PNFC_6M` | Deuda con proveedores no financieros, actual sobre la de hace 6 meses | deuda PNFC_t / deuda PNFC_(t-5) | sin deuda PNFC hace 6 meses | + | modelo |
| `CEN_FLG_FIDEICOMISO_RECUPERO_12M` | Tuvo deuda con un fideicomiso de recupero de cartera en los últimos 12 meses | deuda en t-11..t con una entidad marcada recupero = 1 en referencias/entidades.csv | nunca | + | modelo |

Notas:
- `CEN_FLG_FIDEICOMISO_RECUPERO_12M`: Marca por nombre (Recupero, Asset Recovery, Gestión de Activos); los fideicomisos sin nombre en el maestro quedan sin marcar.

<a id="b08_foto_mensual"></a>
## B08_FOTO_MENSUAL — Campos que sólo trae el mensual (atraso, refinanciación, garante)

| Variable | Descripción | Definición | Nulos | Sentido | Uso |
|---|---|---|---|---|---|
| `CEN_MAX_DIAS_ATRASO_1M` | Máximo de días de atraso informado entre entidades | MAX(dias_atraso) en t | ninguna entidad informa días de atraso para el CUIT | + | futuro |
| `CEN_CNT_ENT_REFINANCIACION_1M` | Entidades que informan REFINANCIACION = 1 | COUNT entidades con el campo REFINANCIACION = 1 en t | nunca (9 = no aplica cuenta como 0) | + | futuro |
| `CEN_CNT_ENT_RECATEG_OBLIG_1M` | Entidades que informan RECATEG_OBLIG = 1 | COUNT entidades con el campo RECATEG_OBLIG = 1 en t | nunca (9 = no aplica cuenta como 0) | + | futuro |
| `CEN_CNT_ENT_SIT_JURIDICA_1M` | Entidades que informan SIT_JURIDICA = 1 | COUNT entidades con el campo SIT_JURIDICA = 1 en t | nunca (9 = no aplica cuenta como 0) | + | futuro |
| `CEN_CNT_ENT_IRRECUP_DISP_TECNICA_1M` | Entidades que informan IRRECUP_DISP_TECNICA = 1 | COUNT entidades con el campo IRRECUP_DISP_TECNICA = 1 en t | nunca (9 = no aplica cuenta como 0) | + | futuro |
| `CEN_FLG_GARANTE_1M` | Figura como garante (garantías otorgadas > 0) en alguna entidad | SUM(garantias_otorgadas) > 0 en t | nunca | ? | futuro |
| `CEN_PCT_DEUDA_GTIA_PREF_1M` | Proporción de la deuda con garantía preferida A o B | SUM(gtia_pref_a + gtia_pref_b) / SUM(prestamos) en t | sin préstamos en el mes | - | futuro |

Notas:
- `CEN_MAX_DIAS_ATRASO_1M`: Lo informan 162 de 545 entidades, casi sólo para situación 2+.
- `CEN_CNT_ENT_REFINANCIACION_1M`: Refinanciación, situación jurídica e irrecuperable sólo aplican a bancos y compañías financieras.
- `CEN_CNT_ENT_RECATEG_OBLIG_1M`: Refinanciación, situación jurídica e irrecuperable sólo aplican a bancos y compañías financieras.
- `CEN_CNT_ENT_SIT_JURIDICA_1M`: Refinanciación, situación jurídica e irrecuperable sólo aplican a bancos y compañías financieras.
- `CEN_CNT_ENT_IRRECUP_DISP_TECNICA_1M`: Refinanciación, situación jurídica e irrecuperable sólo aplican a bancos y compañías financieras.
- `CEN_FLG_GARANTE_1M`: Única aproximación a la codeudoría del CRC.

<a id="b09_persona"></a>
## B09_PERSONA — Atributos de la persona (padrón, 1DSF)

| Variable | Descripción | Definición | Nulos | Sentido | Uso |
|---|---|---|---|---|---|
| `CEN_VAL_EDAD_1M` | Edad en años a la fecha de observación (personas humanas) | años entre fecha_nac_contrato y el último día del mes de observación | persona jurídica o fecha no informada (1901-01-01) | - | modelo |
| `CEN_VAL_ANTIG_EMPRESA_1M` | Años desde el contrato social a la fecha de observación (personas jurídicas) | años entre fecha_nac_contrato y el último día del mes de observación | persona humana o fecha no informada | - | modelo |
| `CEN_CAT_TIPO_PERSONA_1M` | Persona humana o jurídica según prefijo del CUIT | prefijo 20/23/24/27 = HUMANA; 30/33/34 = JURIDICA; resto = OTRO | nunca | ? | modelo |
| `CEN_FLG_ACTIVIDAD_ARCA_1M` | Tiene actividad registrada en ARCA (monotributo, autónomo, empleador) | actividad > 0 en el padrón | CUIT no encontrado en el padrón | - | modelo |
| `CEN_CAT_SECTOR_ACTIVIDAD_1M` | Sector de la actividad principal registrada en ARCA | actividad agrupada a sección del nomenclador (primeros dígitos, ver Actividades_ARCA) | sin actividad registrada | ? | modelo |
| `CEN_CAT_PROVINCIA_1M` | Provincia del domicilio fiscal | provincia del padrón (código ARCA 00-24) | CUIT no encontrado en el padrón | ? | modelo |
| `CEN_CNT_MESES_SIT1_ININT_1M` | Meses en situación 1 ininterrumpida en todas las entidades, sin tope de 24 meses | t - desde_sit1 si desde_sit1 <= t (1DSF del corte vigente); si el deudor tuvo situación 2+ entre t y el corte, se reconstruye con el 24DSF. 0 si SIT_t >= 2. | ausente de la central en t | - | modelo |
| `CEN_FLG_FALLECIDO_1M` | Persona fallecida a la fecha de observación | fecha_fallecimiento <= fin del mes de observación (19010101 = sucesión sin fecha, se considera fallecido) | nunca | + | exclusion |
| `CEN_FLG_CUIT_REEMPLAZADO_1M` | La historia incluye un CUIT dado de baja que fue reemplazado por el actual | existe cuit_baja con cuit_reemplazo = CUIT y deuda informada bajo cuit_baja en t-11..t | nunca | ? | modelo |
| `CEN_CAT_SEXO_1M` | Sexo registrado en ARCA | sexo del padrón (M, F, sin registrar) | sin registrar | n/a | monitoreo |

Notas:
- `CEN_FLG_ACTIVIDAD_ARCA_1M`: Foto del padrón (20261030); para fechas de observación pasadas puede filtrar información futura. Medir PSI y poder por fecha.
- `CEN_CAT_SECTOR_ACTIVIDAD_1M`: Misma salvedad de foto que CEN_FLG_ACTIVIDAD_ARCA_1M.
- `CEN_CNT_MESES_SIT1_ININT_1M`: El 43% de los deudores en situación 1 lo está desde antes de 2020.
- `CEN_FLG_FALLECIDO_1M`: Se excluye de la población de modelado; 219 mil deudores con deuda vigente, 79% en situación 3+.
- `CEN_CAT_SEXO_1M`: No entra al modelo (riesgo de discriminación y regulatorio). Sólo para medir sesgos del score.

<a id="b10_alertas"></a>
## B10_ALERTAS — Combinaciones de señales tempranas

| Variable | Descripción | Definición | Nulos | Sentido | Uso |
|---|---|---|---|---|---|
| `CEN_FLG_ALERTA_DETERIORO_3M` | Pasó de situación 1 a situación 2 o peor en los últimos 3 meses | SIT_t >= 2 AND SIT_(t-2) = 1 | nunca | + | modelo |
| `CEN_FLG_ALERTA_CONTAGIO_3M` | Entró en situación 2 o peor en dos o más entidades en los últimos 3 meses | CEN_CNT_ENT_EMPEORA_3M >= 2 AND ENT_IRR_t >= 2 | nunca | + | modelo |
| `CEN_FLG_ALERTA_SOBREENDEUDAMIENTO_6M` | Deuda creciendo fuerte con relaciones nuevas | CEN_TEN_DEUDA_6M >= 1.5 AND CEN_CNT_ALTAS_6M >= 2 | nunca | + | modelo |
| `CEN_FLG_ALERTA_REFUGIO_NO_BANCARIO_6M` | Cerró relaciones bancarias y abrió no bancarias en los últimos 6 meses | bajas con BANCO o CIA_FIN >= 1 AND altas con TARJETA_NB, PNFC o FIDEICOMISO >= 1 en t-5..t | nunca | + | modelo |
| `CEN_FLG_ALERTA_REINCIDENCIA_12M` | Se curó de situación 2+ y volvió a caer en los últimos 12 meses | secuencia SIT >= 2, luego SIT = 1, luego SIT >= 2 dentro de t-11..t | nunca | + | modelo |
| `CEN_CNT_ALERTAS_1M` | Cantidad de alertas activas | suma de las CEN_FLG_ALERTA_* | nunca | + | modelo |

Notas:
- `CEN_FLG_ALERTA_SOBREENDEUDAMIENTO_6M`: Umbrales iniciales; se calibran con el informe de poder.

## Cómo agregar o cambiar una variable

1. Editar `catalogo/variables.yaml` (o agregar un valor a `expandir` de una familia existente).
2. `cendeu catalogo` valida nombre, operación, bloque, fuente, tipo y uso.
3. Agregar el caso a `tests/test_v2.py` si la lógica es nueva.
4. `cendeu documentar` regenera este documento y el diccionario.
