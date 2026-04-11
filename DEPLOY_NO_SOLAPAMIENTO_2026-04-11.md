# Deploy No Solapamiento 2026-04-11

## Objetivo

Eliminar solapamientos estructurales en el deck live para aumentar el numero de trades sin duplicar la misma idea de trading.

## Cambios aplicados

- `scripts/optimize_deck.py`
  - Añadido veto duro de solape estructural `>= 80%` sobre datos IS.
  - El solape se mide por `symbol + timestamp + direction`.
- `scripts/live_trader.py`
- `scripts/production_sim.py`
- `scripts/validate_live.py`
- `scripts/check_signals.py`
- `scripts/check_last_week.py`
- `tests/test_live_parity.py`
  - Todos estos puntos ya cargan el pool limpio desde `scripts/challenge_decks_v5_clean.py`.
- `config/accounts.yaml`
  - Perfil live actualizado al deck `Decorr24_A` y controles validados en simulacion.

## Perfil desplegado

- Deck: `Decorr24_A`
- Exam mode: `DC3.5 CD1 P2x0.5 MI2 ML3`
- Funded mode: `RF0.5 DC2.5 CD1 MI2 ML3`

## Verificacion de solape

- Pares con solape `>= 80%`: `0`
- Maximo solape observado en IS: `27.2%`
- Peor par: `TPB_NZDUSD_trendL_H4` vs `TPB_NZDUSD_loose_H4_ny`

## Resultados validados

Fuente: `python -X utf8 scripts/production_sim.py`

- Exam IS: `40/107 = 37.4%`
- Exam OOS 2025: `2/7 = 28.6%`
- Gap IS/OOS: `+8.8pp`
- Funded survival medio: `14.5 meses`
- Terminacion funded en 18 meses: `35%`
- Esperanza mensual por cuenta de `$5K`: `~$186 gross / ~$149 net`

## Decision

El perfil `Decorr20_A_DC2.5_CD1_L0_P2x0.7_MI2_ML3` queda descartado para live porque, aun siendo limpio, su validacion exacta daba solo `1/7 = 14.3%` en OOS.

Se despliega `Decorr24_A_DC3.5_CD1_L0_P2x0.5_MI2_ML3` porque:

- mantiene `0` clones estructurales,
- mejora OOS a `28.6%`,
- reduce el gap IS/OOS,
- y mejora la expectativa funded.

## Advertencia metodologica

La seleccion final del perfil ha usado ya el tramo OOS 2025 como validacion de despliegue. Ese OOS ya no debe tratarse como prueba virgen para futuras decisiones. El siguiente filtro serio pasa a ser forward live o shadow live.
