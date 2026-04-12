# Mapa Base del Sistema

Estado de referencia: 2026-04-12.

## Propósito

Este documento fija el baseline técnico del proyecto tal como existe hoy en el repositorio. Su objetivo es responder cuatro preguntas:

1. Cómo separa el sistema la lógica de aprobación (`phase1` y `phase2`) de la lógica de explotación (`funded`).
2. Cómo fluye el trabajo desde research hasta simulación y live.
3. Qué controles de riesgo, estado y trazabilidad están realmente implementados.
4. Qué partes siguen siendo supuestos, cobertura parcial o huecos de validación.

## Regla de evidencia usada en este baseline

- Cada afirmación importante se apoya en `Config`, `Código`, `Test` o `Resultado`.
- Si algo no está demostrado, queda marcado como `Hipótesis`.

## Anclas operativas

- Fuente de verdad viva del deck y de los modos operativos: [`config/accounts.yaml`](../config/accounts.yaml).
- Configuración general de riesgo y backtest: [`config/default.yaml`](../config/default.yaml).
- Cambio de fase, caps diarios y controles por cuenta: [`algosbz/live/account_manager.py`](../algosbz/live/account_manager.py).
- Ejecución live, persistencia y logging de trades: [`scripts/live_trader.py`](../scripts/live_trader.py).
- Validación del perfil desplegado: [`scripts/production_sim.py`](../scripts/production_sim.py).
- Capa de equivalencia y sanity checks: [`scripts/validate_live.py`](../scripts/validate_live.py), [`tests/test_live_runtime.py`](../tests/test_live_runtime.py), [`tests/test_live_parity.py`](../tests/test_live_parity.py).

**Evidencia usada**

- `Config`: [`config/accounts.yaml`](../config/accounts.yaml), [`config/default.yaml`](../config/default.yaml)
- `Código`: [`algosbz/live/account_manager.py`](../algosbz/live/account_manager.py), [`scripts/live_trader.py`](../scripts/live_trader.py), [`scripts/production_sim.py`](../scripts/production_sim.py)
- `Resultado`: README identifica `config/accounts.yaml` como fuente de verdad operativa.

## 1. Separación actual entre lógica de aprobación y lógica funded

### Lo que sí está implementado

- La cuenta live tiene un `state` explícito: `phase1`, `phase2` o `funded`.
- `accounts.yaml` define dos perfiles operativos distintos: `exam_mode` y `funded_mode`.
- `AccountState.active_config` conmuta entre `exam_mode` y `funded_mode` según el estado de la cuenta.
- `AccountState.risk_per_trade` aplica `p2_risk_factor` solo en `phase2`, por lo que `phase1` y `phase2` no comparten exactamente el mismo sizing.
- `AccountState.check_phase_transition()` cambia `phase1 -> phase2 -> funded` y resetea equity, `trading_days` y `total_pnl` al pasar de fase.
- `production_sim.py` replica esta separación cargando `exam_mode`, `funded_mode` y `deck` desde `accounts.yaml`, y usando simuladores distintos para examen y para funded.

**Evidencia usada**

- `Config`: [`config/accounts.yaml`](../config/accounts.yaml), claves `state`, `exam_mode`, `funded_mode`, `deck`.
- `Código`: [`algosbz/live/account_manager.py`](../algosbz/live/account_manager.py), propiedades `active_config`, `risk_per_trade`, método `check_phase_transition()`.
- `Código`: [`scripts/production_sim.py`](../scripts/production_sim.py), `load_deployment_profile()`, `simulate_exam()`, `simulate_funded()`.

### Lo que no debe asumirse sin prueba adicional

- El hard stop live por drawdown es prácticamente uniforme entre estados: `can_trade()` corta por buffer de 4% diario y 9% total independientemente de si la cuenta está en examen o funded.
- El hecho de tener dos modos no demuestra por sí solo que la estrategia funded esté optimizada para extraer más valor; demuestra solo que el runtime permite parámetros distintos.
- `validate_live.py` no usa el `state` grabado en el trade log para verificar si cada trade se ejecutó bajo la fase correcta.

**Evidencia usada**

- `Código`: [`algosbz/live/account_manager.py`](../algosbz/live/account_manager.py), método `can_trade()`.
- `Código`: [`scripts/validate_live.py`](../scripts/validate_live.py), matching por combo/tiempo/dirección/SL sin chequeo explícito de fase.
- `Hipótesis`: la agresividad funded sigue limitada por buffers hard comunes hasta que se demuestre otra política.

## 2. Matriz de control por fase

| Fase | Objetivo operativo actual | Riesgo/control activo | Criterio de éxito modelado | Restricciones modeladas | Módulos ancla |
| --- | --- | --- | --- | --- | --- |
| `phase1` | Aprobar primer escalón del examen con control de DD y días mínimos | `exam_mode`: `risk_per_trade=2.0%`, `daily_cap_pct=3.5`, `cooldown=1`, `max_instr_per_day=2`, `max_daily_losses=3` | Alcanzar `+10%` y `>=4` días operados | Profit target, caps propios, buffer hard live `4%` diario / `9%` total | `config/accounts.yaml`, `algosbz/live/account_manager.py`, `scripts/production_sim.py` |
| `phase2` | Completar segundo escalón con menor agresividad efectiva | Mismo `exam_mode` pero con `p2_risk_factor=0.5`, riesgo efectivo `1.0%` | Alcanzar `+5%` y `>=4` días operados | Mismas restricciones soft del modo examen, más el buffer hard live | `config/accounts.yaml`, `algosbz/live/account_manager.py`, `scripts/production_sim.py` |
| `funded` | Explotar la cuenta ya fondeada con riesgo menor y mayor supervivencia | `funded_mode`: `risk_per_trade=1.0%`, `daily_cap_pct=2.5`, `cooldown=1`, `max_instr_per_day=2`, `max_daily_losses=3` | Sin profit target; éxito modelado como supervivencia media y expectancy mensual | Soft controls específicos funded + mismo buffer hard live | `config/accounts.yaml`, `scripts/production_sim.py`, `scripts/live_trader.py` |

**Evidencia usada**

- `Config`: [`config/accounts.yaml`](../config/accounts.yaml), secciones `exam_mode` y `funded_mode`.
- `Código`: [`algosbz/live/account_manager.py`](../algosbz/live/account_manager.py), `risk_per_trade`, `target_reached()`, `check_phase_transition()`.
- `Código`: [`scripts/production_sim.py`](../scripts/production_sim.py), impresión de perfil desplegado y simulación separada por fase.

## 3. Flujo actual: research -> simulación -> live

### Research y selección

- La investigación de decks y combos vive en scripts, no en un módulo único de research.
- `production_sim.py` no optimiza; toma el perfil desplegado desde `accounts.yaml` y lo revalida sobre histórico.
- `optimize_deck.py`, `walk_forward.py` y scripts afines siguen siendo el laboratorio de búsqueda y comparación.

**Evidencia usada**

- `Código`: [`scripts/production_sim.py`](../scripts/production_sim.py), docstring y `load_deployment_profile()`.
- `Código`: scripts `optimize_deck.py`, `walk_forward.py`, `challenge_decks.py`.

### Simulación

- El backtest usa señal en barra cerrada y ejecución en la apertura de la siguiente barra.
- El motor ajusta SL/TP por gap entre `ref_price` y precio de ejecución para preservar la distancia modelada.
- `RiskManager` limita tamaño por presupuesto de riesgo restante diario y total.

**Evidencia usada**

- `Código`: [`algosbz/backtest/engine.py`](../algosbz/backtest/engine.py), patrón `pending_signal`.
- `Código`: [`algosbz/risk/manager.py`](../algosbz/risk/manager.py), `evaluate_signal()`.
- `Config`: [`config/default.yaml`](../config/default.yaml), `slippage_pips`, `commission_per_lot`, `pessimistic_fills`.

### Live

- `live_trader.py` carga `deck`, `symbol_map`, `exam_mode` y `funded_mode` desde `accounts.yaml`.
- `LiveAccount.evaluate_signal()` replica el paso de filtrado y sizing con `RiskManager` y `EquityManager`.
- El estado runtime y las señales pendientes sobreviven reinicios mediante `data/live_state.json`.
- El historial operativo se guarda en `data/trade_history.jsonl`.

**Evidencia usada**

- `Código`: [`scripts/live_trader.py`](../scripts/live_trader.py), `LiveAccount.evaluate_signal()`, `save_state()`, `load_state()`, `save_trade_log()`.
- `Config`: [`config/accounts.yaml`](../config/accounts.yaml), `deck`, `symbol_map`.

## 4. Controles de riesgo, estado y trazabilidad ya implementados

- Control por cuenta: cooldown por combo, máximo de trades por instrumento y máximo de pérdidas diarias.
- Control por estado de examen: si se alcanza el target antes de los días mínimos, `target_reached()` bloquea trading real y deja la cuenta esperando micro-operativa.
- Frontera de día de trading configurable mediante `daily_reset_hour` y `daily_reset_timezone`.
- Persistencia de estado runtime: counters diarios, open positions y pending signals.
- Logging de entradas y cierres: `OPEN`, `OPEN_RECOVERED` y `CLOSE`.

**Evidencia usada**

- `Código`: [`algosbz/live/account_manager.py`](../algosbz/live/account_manager.py), `can_trade()`, `target_reached()`, `runtime_state_payload()`.
- `Código`: [`algosbz/live/runtime.py`](../algosbz/live/runtime.py), `trading_day_key()`.
- `Código`: [`scripts/live_trader.py`](../scripts/live_trader.py), `save_state()`, `load_state()`, `save_trade_log()`.
- `Test`: [`tests/test_live_runtime.py`](../tests/test_live_runtime.py).

## 5. Baseline de validación ejecutado en este ciclo

| Comando | Estado | Resultado observado | Lectura correcta |
| --- | --- | --- | --- |
| `python -m pytest -q tests/test_live_runtime.py` | `PASS` | La suite runtime pasó en local en esta baseline | Hay cobertura útil sobre estado runtime y controles básicos, pero no sobre toda la cadena live |
| `python -m pytest -q tests/test_live_parity.py` | `TIMEOUT` | No completó dentro de 304 segundos | No sirve hoy como puerta rápida de release; sigue siendo una prueba pesada |
| `python -X utf8 scripts/validate_live.py --offline` | `PARCIAL` | `0 matched`, `3 live-only`, `0 mismatches` | No detectó un bug grueso, pero la evidencia está contaminada y no prueba equivalencia fuerte |

**Evidencia usada**

- `Resultado`: ejecuciones locales del 2026-04-12.
- `Test`: [`tests/test_live_runtime.py`](../tests/test_live_runtime.py), [`tests/test_live_parity.py`](../tests/test_live_parity.py).
- `Código`: [`scripts/validate_live.py`](../scripts/validate_live.py).

## 6. Huecos de trazabilidad o validación que quedan abiertos

### Gap 1. El log histórico de parity no es limpio

- `data/trade_history.jsonl` contiene eventos del account `TEST` generados por tests previos.
- La contaminación futura queda mitigada aislando los tests, pero el archivo histórico sigue sin saneamiento.

**Evidencia usada**

- `Resultado`: [`data/trade_history.jsonl`](../data/trade_history.jsonl) contiene entradas `OPEN_RECOVERED` del account `TEST`.
- `Test`: [`tests/test_live_runtime.py`](../tests/test_live_runtime.py).

### Gap 2. `validate_live.py` verifica una equivalencia parcial

- Compara principalmente tiempo, dirección y SL.
- No verifica volumen, TP, motivo de rechazo, contexto de fase, gap ajustado ni PnL de cierre.

**Evidencia usada**

- `Código`: [`scripts/validate_live.py`](../scripts/validate_live.py), lógica de matching y resumen.

### Gap 3. Falta una puerta de parity rápida y barata

- El test de deck completo es demasiado caro para funcionar como check frecuente.
- Falta una versión smoke sobre pocos combos y rango corto que sirva como semáforo previo a despliegue.

**Evidencia usada**

- `Resultado`: timeout de `python -m pytest -q tests/test_live_parity.py` en esta baseline.
- `Hipótesis`: el cuello principal está en volumen de datos/cómputo más que en un fallo funcional concreto.

### Gap 4. La persistencia de transición de fase a YAML no está cerrada por test dedicado

- La lógica de transición existe y el loop live llama a `save_account_states()`, pero no hay una prueba de integración dedicada sobre esa persistencia.

**Evidencia usada**

- `Código`: [`algosbz/live/account_manager.py`](../algosbz/live/account_manager.py), `check_phase_transition()` y `save_account_states()`.
- `Código`: [`scripts/live_trader.py`](../scripts/live_trader.py), manejo de transiciones en el loop principal.

## 7. Workflow operativo adoptado desde esta baseline

1. Toda idea persistente nueva se documenta en `docs/ideas/`.
2. Todo cambio relevante de Claude se audita en `docs/audits/`.
3. Toda ejecución o investigación de parity se registra en `docs/parity/`.
4. No se aceptan conclusiones de edge, funded rate o parity sin una sección explícita de evidencia.
5. Los comandos reproducibles del proyecto usarán `python -m pytest`, no `pytest` a secas.

**Evidencia usada**

- `Resultado`: `pytest -q tests/test_live_runtime.py` falla por resolución del paquete; `python -m pytest -q tests/test_live_runtime.py` funciona.
- `Código`: [`docs/README.md`](./README.md).
