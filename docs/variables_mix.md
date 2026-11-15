# Variables mix: CENDEU + transaccionalidad propia

Fase 4b del [plan v2](../PLAN.md). Qué variables se pueden construir cruzando la Central de Deudores con datos propios, según la entidad **informe o no informe** a CENDEU. Es un catálogo de diseño: ninguna de estas variables está implementada todavía, porque dependen de datos que no están en este repo.

## Por qué combinar

CENDEU muestra **cuánto debe** una persona y **cómo paga** en todo el sistema, pero con tres límites:
- Es mensual y llega con atraso: el corte de un mes se publica unas cinco semanas después (202609 se procesó el 05/11/2026).
- Sólo trae saldos y situación: no hay cuotas, ingresos, plazos ni movimientos.
- No ve deudas fuera del sistema registrado (informales, comercios que no informan).

La transaccionalidad propia (cuentas, acreditaciones, consumos, pagos, mora interna diaria) es inmediata y granular, pero sólo ve la relación con la propia entidad. Las variables mix combinan las dos miradas: **capacidad de pago** (lo propio) frente a **carga y comportamiento en el sistema** (CENDEU), y **lo que pasa adentro** frente a **lo que pasa afuera**.

## Los dos escenarios

| | A. Entidad que informa a CENDEU | B. Entidad que no informa |
|---|---|---|
| Ejemplos | Bancos, compañías financieras, PNFC inscriptos, emisoras de tarjetas | Fintechs no inscriptas, comercios con crédito propio, billeteras, una entidad antes de otorgar el primer crédito |
| Se ve en CENDEU | Sí: su propia fila (código de entidad) | No: sólo la deuda del cliente con terceros |
| Qué permite | Separar **adentro / afuera** con el mismo dato del BCRA; medir participación en la deuda del cliente | Comparar su exposición propia (dato interno) con la deuda en el sistema; usar CENDEU como única vista externa |
| Cuidado | Excluir la propia fila para las variables "afuera"; el propio reporte puede diferir del interno por fechas de corte | Su propio crédito no aparece en CENDEU: la deuda total real = CENDEU + exposición propia |

## Nomenclatura propuesta

`MIX_<OPERACION>_<DESCRIPCION>_<VENTANA>`, con las mismas operaciones y ventanas que `CEN_*`. Si una variable usa sólo la fila propia en CENDEU (escenario A) se mantiene `CEN_` con el sufijo de descripción `PROPIA`/`AFUERA`; `MIX_` queda para las que necesitan datos internos.

## Variables candidatas

Escenario: **A** = sólo entidad que informa, **B** = sólo la que no informa, **AB** = ambas.

### 1. Participación y relación (adentro / afuera)

| Variable | Definición | Esc. | Datos propios | Hipótesis |
|---|---|---|---|---|
| `CEN_PCT_DEUDA_PROPIA_1M` | Deuda con la propia entidad / deuda total en CENDEU | A | — (fila propia) | Baja participación = cliente con más compromisos afuera; también oportunidad comercial |
| `CEN_FLG_PRINCIPAL_PROPIA_1M` | La propia entidad es la principal del cliente | A | — | Ser principal mejora la información y el recupero |
| `CEN_TEN_DEUDA_AFUERA_6M` | Deuda fuera de la propia entidad, actual / hace 6 meses | A | — | Crecimiento afuera después de originar = sobreendeudamiento |
| `CEN_CNT_ALTAS_AFUERA_POST_ORIG_6M` | Relaciones nuevas con otras entidades en los 6 meses posteriores al alta propia | A | Fecha de originación | Tomar crédito en cadena después de obtenerlo acá |
| `MIX_PCT_EXPOSICION_PROPIA_1M` | Exposición interna / (deuda CENDEU + exposición interna) | B | Saldo propio | Equivalente de participación cuando la entidad no figura en CENDEU |
| `MIX_FLG_REFUGIO_1M` | Tiene deuda vigente propia y en los últimos 6 meses cerró relaciones bancarias y abrió no bancarias | AB | Saldo propio | El cliente llega cuando lo cortan en el sistema (selección adversa) |

### 2. Deterioro afuera antes que adentro

| Variable | Definición | Esc. | Datos propios | Hipótesis |
|---|---|---|---|---|
| `CEN_MAX_SIT_AFUERA_3M` | Peor situación en otras entidades, últimos 3 meses | A | — | La mora suele aparecer primero en el acreedor que el cliente prioriza menos |
| `MIX_FLG_AL_DIA_ADENTRO_MORA_AFUERA_1M` | Al día internamente (0 días de atraso) y situación ≥2 en otra entidad | AB | Días de atraso internos | Alerta temprana: va a llegar acá |
| `MIX_FLG_MORA_ADENTRO_AL_DIA_AFUERA_1M` | Atraso interno > 30 días y situación 1 en el resto del sistema | AB | Días de atraso internos | Problema específico con la entidad (reclamo, error operativo) o prioridad de pago baja |
| `MIX_DIF_ATRASO_INTERNO_VS_SIT_1M` | Días de atraso internos menos el atraso implícito por la situación BCRA de la propia fila | A | Días de atraso internos | Desfasajes de reporte; control de calidad del propio envío |
| `MIX_RCM_DETERIORO_AFUERA_VS_ADENTRO_12M` | Meses entre la primera situación ≥2 afuera y el primer atraso interno > 30 días | AB | Historia de mora interna | Mide cuánta anticipación da CENDEU para este cliente |

### 3. Capacidad de pago

CENDEU trae saldos, no cuotas. Para estimar la carga mensual se usa un plazo de referencia por tipo de entidad (p. ej. tarjetas: pago mínimo; préstamos personales: 24 meses), a calibrar con la cartera propia.

| Variable | Definición | Esc. | Datos propios | Hipótesis |
|---|---|---|---|---|
| `MIX_RAT_DEUDA_SISTEMA_INGRESO_1M` | Deuda total CENDEU (+ propia en B) / ingreso mensual estimado (acreditaciones promedio de 3 meses) | AB | Acreditaciones de haberes o ingresos | Apalancamiento: el clásico deuda/ingreso con deuda de todo el sistema |
| `MIX_RAT_CUOTA_ESTIMADA_INGRESO_1M` | Cuota estimada del sistema / ingreso mensual | AB | Ingresos | Carga financiera mensual (DTI) |
| `MIX_TEN_DEUDA_VS_INGRESO_6M` | Tendencia de la deuda del sistema dividida por la tendencia del ingreso, 6 meses | AB | Ingresos | Deuda que crece más rápido que el ingreso |
| `MIX_RAT_SALDO_CUENTA_DEUDA_IRR_1M` | Saldo promedio en cuenta / deuda irregular en el sistema | AB | Saldos de cuenta | Liquidez para regularizar (análogo a la cobertura con depósitos del CRC) |
| `MIX_FLG_CAIDA_INGRESO_CON_DEUDA_CRECIENTE_3M` | Ingreso cae > 20% y la deuda del sistema crece en el mismo trimestre | AB | Ingresos | Señal combinada de estrés |

### 4. Pagos observados a otros acreedores

Con la transaccionalidad se ven débitos y transferencias hacia entidades financieras (por CUIT del destinatario o CBU). Cruzados con CENDEU:

| Variable | Definición | Esc. | Datos propios | Hipótesis |
|---|---|---|---|---|
| `MIX_PCT_ENT_CENDEU_CON_PAGO_OBSERVADO_3M` | Entidades de CENDEU a las que se observa un pago desde la cuenta propia / entidades en CENDEU | AB | Débitos y transferencias con destinatario identificado | Cuanto más paga desde acá, más visible es su flujo |
| `MIX_FLG_PAGO_A_ACREEDOR_NO_INFORMADO_3M` | Pagos recurrentes a un prestamista que no figura en CENDEU | AB | Ídem | Deuda fuera del sistema registrado |
| `MIX_FLG_DEJO_DE_PAGAR_ACREEDOR_3M` | Pagos recurrentes a una entidad que se interrumpen | AB | Ídem | Anticipa la situación ≥2 que CENDEU mostrará uno o dos meses después |

### 5. Uso del propio producto frente al sistema

| Variable | Definición | Esc. | Datos propios | Hipótesis |
|---|---|---|---|---|
| `MIX_RAT_CONSUMO_TC_PROPIA_DEUDA_TARJETA_SISTEMA_1M` | Consumo con la tarjeta propia / deuda con emisoras de tarjetas en CENDEU | AB | Consumos | Desplazamiento del gasto hacia el producto propio |
| `MIX_TEN_USO_LINEA_PROPIA_VS_DEUDA_SISTEMA_3M` | Tendencia del uso de la línea propia dividida por la tendencia de la deuda del sistema | AB | Uso de línea | Uso intensivo del crédito propio mientras crece la deuda afuera |
| `MIX_FLG_CAMBIO_COMPORTAMIENTO_1M` | Cambio brusco en la transaccionalidad propia (gasto, saldo, frecuencia) coincidente con un empeoramiento en CENDEU | AB | Transaccionalidad general | Confirma el deterioro con dos fuentes |

## Reglas para construirlas

1. **Alineación temporal (*point-in-time*)**: a la fecha de decisión *d* sólo se puede usar el último corte de CENDEU publicado antes de *d* (hoy, el mes *d* − 2 aproximadamente) y la transaccionalidad hasta *d*. Mezclar el corte del mismo mes que la decisión introduce información del futuro y sobreestima el poder.
2. **Misma grilla**: la transaccionalidad se agrega por CUIT × mes calendario, con ausencia explícita (0) como en las series `CEN_*`.
3. **Escenario A**: definir una sola vez el código (o los códigos, tras fusiones) de la propia entidad en `referencias/entidades_equivalencias.csv`, y calcular las variables "afuera" excluyendo esa fila.
4. **Escenario B**: la deuda total real es CENDEU + exposición propia; no mezclar la exposición propia en las variables `CEN_*` para que sigan siendo comparables entre entidades.
5. **Montos**: misma deflación por IPC que la v2 ([`referencias/ipc.csv`](../referencias/ipc.csv)); ingresos y saldos propios en pesos constantes del mismo mes base.
6. **Consentimiento y uso**: confirmar con el área legal la base legal para cruzar la información de CENDEU con datos internos del cliente (Ley 25.326) y el uso permitido para originación, seguimiento y cobranza.

## Próximo paso sugerido

Priorizar con el área que tenga los datos propios: las de mayor relación costo/beneficio suelen ser deuda del sistema / ingreso (bloque 3), al día adentro con mora afuera (bloque 2) y participación propia (bloque 1). Se agregan al catálogo como un bloque `B11_MIX` con fuente `PROPIA`, y el informe de poder las evalúa igual que al resto.
