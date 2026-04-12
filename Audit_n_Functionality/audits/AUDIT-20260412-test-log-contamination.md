# AUDIT-20260412-test-log-contamination

## Resumen ejecutivo

- La validación offline de parity estaba leyendo eventos `OPEN_RECOVERED` generados por `tests/test_live_runtime.py` como si fueran trades live reales.
- Se corrigió la higiene del test aislando su escritura de logs a un directorio temporal y se amplió cobertura sobre transiciones de fase y bloqueo por objetivo alcanzado.

## Hallazgos críticos

- Antes de la corrección, `test_reconcile_account_positions_recovers_untracked_mt5_positions()` escribía en `data/trade_history.jsonl` vía `scripts/live_trader.py::save_trade_log()`.
- Ese comportamiento contaminaba la evidencia usada por `scripts/validate_live.py --offline`, que toma `data/trade_history.jsonl` como fuente de verdad de entradas live.

## Hallazgos importantes

- El archivo histórico `data/trade_history.jsonl` ya contiene entradas del pseudo-account `TEST`; la corrección evita contaminación futura, pero no limpia la evidencia antigua.
- La suite runtime ahora cubre la transición `phase1 -> phase2 -> funded`, pero sigue sin cubrir la persistencia de cambio de estado en `config/accounts.yaml` dentro del loop de `scripts/live_trader.py`.

## Mejoras opcionales

- Añadir una prueba de integración pequeña que ejercite `save_account_states()` tras una transición real de fase.
- Separar logs de tests y logs de live en rutas imposibles de compartir por diseño.

## Riesgos específicos para aprobación de fondeo

- Si los informes de parity usan eventos de test como si fueran reales, puede aprobarse una lógica de examen con evidencia falsa o irrelevante.

## Riesgos específicos para explotación de cuentas fondeadas

- La misma contaminación puede ocultar divergencias reales en una cuenta fondeada y retrasar una corrección de riesgo o ejecución.

## Riesgos específicos para backtest/live parity

- `scripts/validate_live.py` puede seguir devolviendo resultados parciales válidos en sintaxis pero inválidos en semántica si el log histórico no se sanea.

## Recomendaciones concretas

- Mantener el aislamiento temporal añadido en tests.
- Tratar `data/trade_history.jsonl` actual como evidencia contaminada hasta que se limpie o se regenere con actividad live real.
- Registrar una nota de parity cada vez que se ejecute `validate_live.py`, indicando si el log usado estaba limpio o no.

## Evidencia usada

- `Código`: [`scripts/live_trader.py`](../../scripts/live_trader.py), funciones `reconcile_account_positions()` y `save_trade_log()`.
- `Test`: [`tests/test_live_runtime.py`](../../tests/test_live_runtime.py), caso `test_reconcile_account_positions_recovers_untracked_mt5_positions`.
- `Resultado`: [`data/trade_history.jsonl`](../../data/trade_history.jsonl) contiene entradas `OPEN_RECOVERED` del account `TEST`.
- `Hipótesis`: ninguna.
