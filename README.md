# AlgoSbz — Algorithmic Prop Firm Exam Factory

Sistema de trading algoritmico diseñado como **fabrica de cuentas fondeadas**. Genera señales automaticamente, pasa examenes FTMO 2-Step (y otras prop firms) y gestiona las cuentas fondeadas en modo conservador para maximizar supervivencia.

**Resultados actuales:** 32.7% IS / 28.6% OOS funded rate con 16 combos decorrelados.

---

## Tabla de Contenidos

1. [Arquitectura General](#1-arquitectura-general)
2. [Estrategias de Trading](#2-estrategias-de-trading)
3. [Combos y Deck de Produccion](#3-combos-y-deck-de-produccion)
4. [Gestion del Riesgo](#4-gestion-del-riesgo)
5. [Entrenamiento y Validacion](#5-entrenamiento-y-validacion)
6. [Sistema Live](#6-sistema-live)
7. [ROI Esperado](#7-roi-esperado)
8. [Setup y Uso](#8-setup-y-uso)

---

## 1. Arquitectura General

```
AlgoSbz/
├── algosbz/                    # Paquete principal
│   ├── core/                   # Config, enums, modelos de datos
│   ├── strategy/               # 13 estrategias de trading
│   ├── backtest/               # Motor de backtesting + broker simulado
│   ├── risk/                   # Position sizing + equity manager
│   ├── data/                   # Carga de datos + resampler
│   └── live/                   # Sistema de trading en vivo
│       ├── mt5_connector.py    # Conexion MetaTrader 5
│       └── account_manager.py  # Estado de cuentas + controles portfolio
├── scripts/
│   ├── challenge_decks.py      # Definicion de combos y decks
│   ├── optimize_deck.py        # Optimizador de funded rate
│   ├── production_sim.py       # Validacion independiente
│   └── live_trader.py          # Orquestador de trading en vivo
├── config/
│   ├── default.yaml            # Config general (riesgo, backtest)
│   ├── instruments.yaml        # Especificaciones por instrumento
│   └── accounts.yaml           # Credenciales MT5 (rellenar)
└── Datos_historicos/           # Datos M1 de Darwinex (CSV)
```

**Flujo de datos:**

```
Datos M1 (CSV) → Resample (H4/H1/M15) → Strategy.setup() → Strategy.on_bar()
→ Signal (LONG/SHORT + SL/TP) → RiskManager (position sizing) → Broker (ejecucion)
```

---

## 2. Estrategias de Trading

### 2.1 Volatility Mean Reversion (VMR)

**Edge:** Fades movimientos extendidos fuera de Bollinger Bands.

Cuando el precio cierra fuera de las bandas durante 2+ velas consecutivas, el movimiento esta sobreextendido y la reversion a la media es probable.

| Parametro | Valor |
|-----------|-------|
| Indicadores | BB(20, 2.5), ATR(14), ADX(14) |
| Timeframe | H1, H4 |
| Entry Long | 2+ cierres bajo BB inferior, ADX < 30 |
| Entry Short | 2+ cierres sobre BB superior, ADX < 30 |
| SL / TP | 3.0 ATR / 4.0 ATR (RR 1:1.33) |
| Filtro | Session hours 7-20 UTC |

### 2.2 Trend Pullback (TPB)

**Edge:** Entra en retrocesos dentro de tendencias fuertes.

Cuando las EMAs (21/50/200) estan alineadas y ADX > 25, el precio retrocediendo a la EMA rapida ofrece entrada de bajo riesgo a favor de la tendencia.

| Parametro | Valor |
|-----------|-------|
| Indicadores | EMA(21/50/200), ATR(14), ADX(14) |
| Timeframe | H1, H4 |
| Entry Long | EMAs bull-aligned, precio pullback a EMA 21, ADX > 25 |
| Entry Short | EMAs bear-aligned, precio pullback a EMA 21, ADX > 25 |
| SL / TP | 2.0 ATR / 3.0-4.0 ATR (RR 1:1.5-2) |
| Variantes | `loose`: pullback_zone 0.7 ATR, TP 4.0 ATR |

### 2.3 Swing Breakout (SwBrk)

**Edge:** Captura movimientos multi-dia tras contraccion de volatilidad.

Despues de un squeeze (ATR < 80% de su media), la primera rotura del canal Donchian con expansion de volatilidad tiende a continuar 2-5 dias por cascadas de stop losses.

| Parametro | Valor |
|-----------|-------|
| Indicadores | ATR(14), ATR MA(50), Donchian(20), ADX(14) |
| Timeframe | H4 |
| Entry Long | Squeeze reciente + expansion + cierre > Donchian high |
| Entry Short | Squeeze reciente + expansion + cierre < Donchian low |
| SL / TP | 1.5 ATR / 3.0 ATR (RR 1:2) |
| Variantes | `slow`: Donchian 30, squeeze 0.75, TP 4.0 ATR |

### 2.4 Engulfing Reversal (Engulf)

**Edge:** Fades extremos con confirmacion de envolvente.

Un patron envolvente en swing extremos (maximos/minimos recientes) señala flujo institucional invirtiendo direccion. Solo en mercados no tendenciales (ADX < 30).

| Parametro | Valor |
|-----------|-------|
| Indicadores | ATR(14), ADX(14) |
| Timeframe | H4 |
| Entry Long | Envolvente alcista en swing low, body ratio >= 60% |
| Entry Short | Envolvente bajista en swing high, body ratio >= 60% |
| SL / TP | 1.5 ATR / 2.5-3.0 ATR (RR 1:1.67-2) |
| Variantes | `tight`: swing_zone 0.3 ATR, body_ratio 70%, TP 3.0 |

### 2.5 Structure Break (StrBrk)

**Edge:** Detecta cambios de estructura de mercado (HH/HL → LH/LL).

Analisis puro de estructura sin indicadores. Cuando la secuencia de swings cambia (e.g., HH/HL hace un LL), señala cambio de tendencia antes de que indicadores lo confirmen.

| Parametro | Valor |
|-----------|-------|
| Indicadores | ATR(14) |
| Timeframe | H1, H4 |
| Entry Long | 3 swing highs descendientes + rotura alcista |
| Entry Short | 3 swing lows ascendientes + rotura bajista |
| SL / TP | 1.5 ATR / 3.0-4.0 ATR (RR 1:2) |
| Variantes | `slow`: swing_lookback 7, TP 4.0 |

### 2.6 Momentum Divergence (MomDiv)

**Edge:** Opera divergencias RSI-precio.

Cuando el precio hace un nuevo maximo pero el RSI hace un maximo mas bajo, el momentum se agota y la reversion es probable.

| Parametro | Valor |
|-----------|-------|
| Indicadores | RSI(14), ATR(14) |
| Timeframe | H1, H4 |
| Entry Short | Precio higher high, RSI lower high (bearish div) |
| Entry Long | Precio lower low, RSI higher low (bullish div) |
| SL / TP | 1.5 ATR / 2.5 ATR (RR 1:1.67) |

### 2.7 Regime-Adaptive VMR (RegVMR)

**Edge:** VMR con filtro de regimen de mercado.

Envuelve la estrategia VMR con un detector de regimen que solo permite trades cuando el mercado es ranging con volatilidad aceptable. Desbloquea VMR en instrumentos donde el VMR puro falla por operar en regimenes tendenciales.

| Parametro | Valor |
|-----------|-------|
| Indicadores | BB, ATR, ADX + RegimeDetector |
| Timeframe | H1 |
| Entry | Misma logica que VMR + regime = ranging |
| SL / TP | 3.0 ATR / 4.0 ATR (RR 1:1.33) |

### 2.8 EMA Ribbon Trend (EMArib)

**Edge:** Trend following con ribbon de 5 EMAs.

Cuando las 5 EMAs (8/13/21/34/55) estan perfectamente alineadas durante 3+ velas, la tendencia es institucional. Entrada en pullbacks de RSI para capturar continuacion.

| Parametro | Valor |
|-----------|-------|
| Indicadores | EMA Ribbon (8/13/21/34/55), RSI(14), ATR(14) |
| Timeframe | H1, H4 |
| Entry Long | Ribbon score >= 0.7 durante 3+ bars, RSI <= 45 |
| Entry Short | Ribbon score <= -0.7 durante 3+ bars, RSI >= 55 |
| SL / TP | 2.0 ATR / 4.0 ATR (RR 1:2) |

### 2.9 Session Breakout (SessBrk)

**Edge:** Captura expansion de volatilidad en apertura London/NY.

Antes de las aperturas principales, el precio consolida. El primer breakout durante kill zones (London 7-10 UTC, NY 12-15 UTC) continua por flujo institucional.

| Parametro | Valor |
|-----------|-------|
| Indicadores | ATR(14), Kill Zone Mask |
| Timeframe | M15 |
| Entry Long | En kill zone + cierre > pre-range high |
| Entry Short | En kill zone + cierre < pre-range low |
| SL / TP | 1.0 ATR / 2.0 ATR (RR 1:2) |
| Nota | Unica estrategia M15 — alta frecuencia relativa |

### 2.10 SMC Order Block (SMCOB)

**Edge:** Entra en zonas institucionales de demanda/oferta.

Los order blocks marcan donde los grandes players acumularon posiciones. Cuando el precio retorna con vela de rechazo (wick largo), la probabilidad de rebote es alta.

| Parametro | Valor |
|-----------|-------|
| Indicadores | ATR(14), Order Blocks, Structure Bias |
| Timeframe | H1, H4 |
| Entry Long | Precio en OB alcista + bias neutral/bull + rechazo |
| Entry Short | Precio en OB bajista + bias neutral/bear + rechazo |
| SL / TP | 1.5 ATR / 3.0 ATR (RR 1:2) |

### 2.11 FVG Reversion (FVGrev)

**Edge:** Reversion hacia Fair Value Gaps (imbalances).

Los FVG actuan como imanes — el precio tiende a rellenarlos. En mercados no tendenciales (trend strength < 25), la reversion al FVG es alta probabilidad.

| Parametro | Valor |
|-----------|-------|
| Indicadores | ATR(14), FVG zones, Trend Strength |
| Timeframe | H1, H4 |
| Entry Long | Precio en FVG alcista, trend strength < 25 |
| Entry Short | Precio en FVG bajista, trend strength < 25 |
| SL / TP | 1.5 ATR / 2.5 ATR (RR 1:1.67) |

### 2.12 VWAP Reversion (VWAPrev)

**Edge:** Reversion a VWAP ponderado por volumen.

VWAP representa valor justo. Desviaciones significativas (> 0.5 ATR) durante sesiones activas son corregidas por rebalanceo institucional.

| Parametro | Valor |
|-----------|-------|
| Indicadores | ATR(14), VWAP (session-based) |
| Timeframe | M15, H1 |
| Entry Long | Precio < VWAP - 0.5 ATR (en kill zone) |
| Entry Short | Precio > VWAP + 0.5 ATR (en kill zone) |
| SL / TP | 1.0 ATR / 1.5 ATR (RR 1:1.5) |

### 2.13 Inside Bar Breakout (IBB)

**Edge:** Breakout de velas interiores con filtro de tendencia.

Una inside bar señala compresion. El breakout del rango de la mother bar, especialmente alineado con la tendencia (EMA 50), captura expansion direccional.

| Parametro | Valor |
|-----------|-------|
| Indicadores | ATR(14), EMA(50) |
| Timeframe | H4 |
| Entry Long | Cierre > mother bar high + EMA trend up |
| Entry Short | Cierre < mother bar low + EMA trend down |
| SL / TP | 1.5 ATR / 3.0 ATR (RR 1:2) |

---

## 3. Combos y Deck de Produccion

### 3.1 Que es un Combo

Un **combo** es la combinacion de: estrategia + instrumento + timeframe + parametros especificos.

Ejemplo: `TPB_XTIUSD_loose_H4` = TrendPullback en petroleo WTI en H4 con parametros loose (pullback zone amplia, TP agresivo).

### 3.2 Pool de Combos Validados

27 combos en total, clasificados en dos tiers:

**ROBUST (16):** Pasaron test de spread +50% Y sensibilidad de parametros ±20%.

| Combo | Estrategia | Instrumento | TF | PF |
|-------|-----------|-------------|-----|-----|
| VMR_SPY_H4 | Vol Mean Rev | S&P 500 | H4 | 1.34 |
| TPB_XTIUSD_loose_H4 | Trend Pullback | WTI Oil | H4 | 1.40 |
| TPB_XNGUSD_loose_H4 | Trend Pullback | Natural Gas | H4 | 1.37 |
| SwBrk_XTIUSD_H4 | Swing Breakout | WTI Oil | H4 | 1.29 |
| SwBrk_SPY_H4 | Swing Breakout | S&P 500 | H4 | 1.05 |
| SwBrk_SPY_slow_H4 | Swing Breakout | S&P 500 | H4 | 1.72 |
| Engulf_EURUSD_tight_H4 | Engulfing Rev | EUR/USD | H4 | 1.33 |
| Engulf_XAUUSD_tight_H4 | Engulfing Rev | Gold | H4 | 1.39 |
| StrBrk_GBPJPY_slow_H4 | Structure Break | GBP/JPY | H4 | 1.21 |
| MomDiv_SPY_H1 | Momentum Div | S&P 500 | H1 | 1.14 |
| RegVMR_XAUUSD_H1 | Regime VMR | Gold | H1 | 1.25 |
| RegVMR_XTIUSD_H1 | Regime VMR | WTI Oil | H1 | 1.35 |
| SessBrk_XTIUSD_M15 | Session Breakout | WTI Oil | M15 | 2.01 |
| SMCOB_GBPJPY_H1 | SMC Order Block | GBP/JPY | H1 | — |
| SMCOB_XAUUSD_H4 | SMC Order Block | Gold | H4 | — |
| SMCOB_XAUUSD_loose_H4 | SMC Order Block | Gold | H4 | — |

**SPREAD_OK (11):** Pasaron test de spread +50% pero no sensibilidad.

### 3.3 Deck de Produccion: Decorr16_A

El deck activo son **16 combos** seleccionados por un algoritmo greedy de decorrelacion:

1. Se calcula la matriz de correlacion entre todos los combos usando solo datos IS (< 2025)
2. El algoritmo greedy selecciona iterativamente el combo con menor correlacion promedio al deck actual, ponderado por PF
3. Se filtran combos con PnL total negativo a riesgo directo del 2%

**Combos en el deck:**

```
SessBrk_XTIUSD_M15      SwBrk_SPY_slow_H4       SMCOB_XAUUSD_loose_H4
Engulf_XAUUSD_tight_H4  TPB_XTIUSD_loose_H4     TPB_XNGUSD_loose_H4
RegVMR_XTIUSD_H1        VMR_SPY_H4              Engulf_EURUSD_tight_H4
SwBrk_XTIUSD_H4         VMR_USDCHF_H1           RegVMR_XAUUSD_H1
StrBrk_GBPJPY_slow_H4   EMArib_XNGUSD_loose_H4  SMCOB_XAUUSD_H4
SwBrk_SPY_fast_H4
```

**Diversificacion:**
- 7 instrumentos: XTIUSD, SPY, XAUUSD, XNGUSD, EURUSD, USDCHF, GBPJPY
- 8 tipos de estrategia: SessBrk, SwBrk, SMCOB, Engulf, TPB, RegVMR, VMR, StrBrk, EMArib
- 3 timeframes: M15, H1, H4

---

## 4. Gestion del Riesgo

### 4.1 Position Sizing

```
lot_size = (equity × risk%) / (SL_pips × pip_value_per_lot)
```

**Ejemplo:** EURUSD, cuenta $100K, riesgo 2%, SL 30 pips:
```
lot_size = ($100,000 × 0.02) / (30 × $10) = $2,000 / $300 = 6.67 lots
```

### 4.2 Costes de Transaccion (Backtest)

| Componente | Valor | Notas |
|-----------|-------|-------|
| Spread | Datos reales de Darwinex | Fallback: instrumento default |
| Slippage | 0.5 pips adverso | Entrada y salida |
| Comision | $7 / lote round-trip | Conservador vs FTMO ($6) |
| Fills | Pesimistas | Si SL y TP se tocan en misma vela, SL tiene prioridad |

### 4.3 Controles de Portfolio (6 dimensiones)

| Control | Exam Mode | Funded Mode | Funcion |
|---------|-----------|-------------|---------|
| Risk per trade | 2.0% | 1.4% | Tamaño de posicion |
| Daily loss cap | 2.5% | 1.5% | Para trading si perdida diaria >= X% |
| Cooldown | 1 loss/combo/dia | 2 losses/combo/dia | Evita revenge trading |
| Max instr/dia | 2 | 2 | Evita perdidas correladas (ej: 3 combos XAUUSD perdiendo mismo dia) |
| Max losses/dia | 3 | 3 | Hard cap en numero de perdidas totales |
| P2 risk factor | 0.5× | N/A | Half risk en Phase 2 (target menor) |

### 4.4 Limites FTMO (Hard Limits)

| Limite | FTMO | Nuestro buffer |
|--------|------|---------------|
| DD Diario | 5% | Paramos en 4% (1% margen) |
| DD Total | 10% (static desde inicial) | Paramos en 9% (1% margen) |

### 4.5 Sistema Anti-Martingala (Equity Manager)

El equity manager ajusta automaticamente el tamaño de posicion basado en:

**Tier de Drawdown (desde balance inicial):**

| DD | Multiplicador | Efecto |
|----|-------------|--------|
| 0-3% | 1.0× | Riesgo completo |
| 3-5% | 0.5× | Mitad de riesgo |
| 5-7% | 0.25× | Cuarto de riesgo |
| 8%+ | 0.0× | Stop total |

**Ramp-up progresivo:** Los primeros 3 trades de una nueva ventana van a 0.5× → 0.67× → 0.83× → 1.0×.

**Bonus por racha:** Despues de 3+ wins consecutivos, +10% por win (max 1.3×).

---

## 5. Entrenamiento y Validacion

### 5.1 Datos

- **Fuente:** Darwinex (datos M1 reales con spread incluido)
- **Periodo:** 2016-2025 (10 años)
- **Instrumentos:** EURUSD, GBPJPY, USDCHF, XAUUSD, XTIUSD, XNGUSD, SPY
- **Timezone:** GMT+2/+3 (EET/EEST) — compatible con FTMO

### 5.2 Metodologia (Libre de Sesgos)

1. **Pre-computo a riesgo directo 2%**: Trades pre-calculados con riesgo real, no escalados linealmente. DD limits relajados (50%) para que el risk manager no mate combos prematuramente — el DD de portfolio se aplica en la simulacion.

2. **Split IS/OOS estricto**: IS = 2016-2024, OOS = 2025. Las estrategias NUNCA vieron datos de 2025 durante el diseño.

3. **Sin look-ahead bias**: El motor de backtest ejecuta señales en la apertura de la siguiente vela (pending signal pattern). Gap adjustment preserva distancias SL/TP.

4. **Simulacion realista de examen**: Ventanas de 30 dias P1 + 60 dias P2 con todos los controles de portfolio activos. Balance se resetea entre fases (regla FTMO confirmada).

5. **OOS truncado**: Ventanas OOS limitadas a las que tienen suficientes datos (90 dias antes del fin de los datos) para evitar windows cortados artificialmente.

### 5.3 Pipeline de Optimizacion

```
optimize_deck.py:
  1. Pre-computo de trades (2% directo, DD relajado)
  2. Filtro PnL negativo (excluir combos no rentables)
  3. Seleccion de deck por decorrelacion (greedy, correlacion IS only)
  4. Grid search: daily_cap × cooldown × lookback × p2_risk × max_instr × max_losses
  5. Evaluacion IS (2016-2024, ventanas cada 30 dias)
  6. Evaluacion OOS (2025, ventanas cada 30 dias)
  7. Estabilidad: top 10 configs, spread IS/OOS
  8. Funded survival grid search (risk_factor × daily_cap × cooldown × max_instr × max_losses)
  9. ROI combinado (exam factory + funded income)
```

### 5.4 Validacion Independiente

```
production_sim.py:
  - Config FIJA (sin optimizacion)
  - Misma logica de simulacion
  - Debe coincidir con optimize_deck.py (cross-check OOS)
  - Incluye funded survival y ROI
```

### 5.5 Resultados

**Exam Mode (Decorr16_A @2% DC2.5 CD1 P2x0.5):**

| Periodo | Funded Rate | N Windows |
|---------|------------|-----------|
| IS (2016-2024) | 32.7% | 107 |
| OOS (2025) | 28.6% | 7 |
| Gap | 4.1pp | — |

**Año por año:**

| Año | Rate | Tipo |
|-----|------|------|
| 2016 | 0% | IS |
| 2017 | 20% | IS |
| 2018 | 50% | IS |
| 2019 | 30% | IS |
| 2020 | 60% | IS |
| 2021 | 50% | IS |
| 2022 | 40% | IS |
| 2023 | 50% | IS |
| 2024 | 40% | IS |
| 2025 | 28.6% | OOS |

---

## 6. Sistema Live

### 6.1 Principio Fundamental: Fidelidad al Backtest

El sistema live replica **exactamente** el flujo del motor de backtest (`BacktestEngine`). Cada decision que toma el live pasa por los mismos componentes con la misma logica:

| Componente Backtest | Componente Live | Funcion |
|---|---|---|
| `pending_signal` | `StrategyManager.pending_signals` | Señal almacenada, ejecutada en siguiente barra |
| Gap adjustment (SL/TP shift) | `get_executable_signals()` | Preserva distancias ATR ante gaps |
| `broker.has_position` | `LiveAccount.open_positions[combo]` | No duplicar entradas por combo |
| `EquityManager.get_risk_multiplier()` | `LiveAccount._equity_manager` (por cuenta) | Anti-martingala con DD tiers |
| `RiskManager.evaluate_signal()` | `LiveAccount._get_risk_manager()` (por instrumento) | Position sizing + DD checks |
| `strategy.setup(df)` una vez | `setup_with_history()` una vez al arrancar | Indicadores pre-calculados |

### 6.2 Arquitectura

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
│ MT5 Terminal │────→│ MT5Connector │────→│  StrategyManager  │
│ (datos live) │     │ (rotacion)   │     │  (16 combos)      │
└─────────────┘     └──────────────┘     └────────┬─────────┘
                                                   │
                                          ┌────────▼─────────┐
                                          │ Pending Signals   │
                                          │ (espera next bar) │
                                          └────────┬─────────┘
                                                   │ next bar open
                                          ┌────────▼─────────┐
                                          │ Gap Adjustment    │
                                          │ SL/TP += gap      │
                                          └────────┬─────────┘
                                                   │
                    ┌──────────────────────────────▼──────────────────────────┐
                    │              PER ACCOUNT (×4)                           │
                    │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
                    │  │ Portfolio    │→ │ EquityManager│→ │ RiskManager  │  │
                    │  │ Controls    │  │ (anti-mart.) │  │ (sizing+DD)  │  │
                    │  │ DC/CD/MI/ML │  │ DD tiers     │  │ evaluate()   │  │
                    │  └──────────────┘  └──────────────┘  └──────┬───────┘  │
                    └─────────────────────────────────────────────┼──────────┘
                                                                  │
                                                         ┌────────▼─────────┐
                                                         │  MT5 Execution   │
                                                         │  (market order)  │
                                                         └──────────────────┘
```

### 6.3 Flujo de Operacion (Fiel al Backtest)

**Arranque:**
1. Conecta a la primera cuenta MT5
2. Descarga 500 barras de historico por cada feed (symbol × timeframe)
3. Ejecuta `strategy.setup(df)` UNA vez por combo (igual que backtest)
4. Inicializa `RiskManager` + `EquityManager` por cuenta

**Loop (cada 30s) — replica exacta del main loop del backtest:**

```
Ciclo N: nueva vela detectada en feed (sym, tf)

  Paso 1: Sync posiciones cerradas
    → Detecta SL/TP ejecutados por MT5 (polling positions)
    → Actualiza EquityManager.on_trade_closed() y RiskManager

  Paso 2: Ejecutar señales PENDIENTES del ciclo anterior
    → Signal generada en bar[i-1] → ejecuta en bar[i] open
    → Gap adjustment: SL/TP += (bar[i].open - bar[i-1].close)
    → EquityManager.get_risk_multiplier() → anti-martingala
    → RiskManager.evaluate_signal() → position sizing + DD checks
    → Solo si !has_position(combo) → no duplicar

  Paso 3: Generar NUEVAS señales
    → strategy.on_bar(idx, bar, has_position) en vela completada
    → Almacenadas como pending → ejecutaran en el PROXIMO ciclo

  Paso 4: Ejecutar ordenes aprobadas via MT5
    → Rotacion de cuentas (una conexion a la vez)
    → Market order con SL/TP
```

**Este flujo de 2 pasos (generar → esperar → ejecutar) es exactamente lo que hace el backtest** con su `pending_signal` pattern. Garantiza que no hay look-ahead bias en live.

### 6.4 Componentes por Cuenta (LiveAccount)

Cada `LiveAccount` contiene:

- **AccountState**: controles de portfolio (daily cap, cooldown, max instr, max losses)
- **EquityManager**: anti-martingala con mismos DD tiers que backtest
  - 0-3% DD → 1.0× | 3-5% → 0.5× | 5-7% → 0.25× | 8%+ → 0.0×
  - Progressive ramp-up: primeros 3 trades a 0.5× → 1.0×
  - Win streak bonus: 3+ wins → +10%/win (max 1.3×)
- **RiskManager** (por instrumento): `evaluate_signal()` identico al backtest
  - Position sizing: `lot = (equity × risk% × multiplier) / (SL_pips × pip_value)`
  - DD budget check: rechaza si remaining_daily o remaining_total <= 0
  - Max positions check
- **open_positions**: dict combo → ticket MT5 (previene duplicados)

### 6.5 Dos Modos de Operacion

| Aspecto | Exam (P1/P2) | Funded |
|---------|-------------|--------|
| Objetivo | Pasar rapido | Sobrevivir largo |
| Risk/trade | 2.0% | 1.4% |
| Daily cap | 2.5% | 1.5% |
| P2 factor | 0.5× | N/A |
| Comportamiento | Agresivo | Conservador |

La transicion es **automatica**: cuando la cuenta alcanza el target + 4 dias de trading, el sistema:
1. Cambia el estado en `accounts.yaml`
2. Reinicializa `EquityManager` y `RiskManager` para la nueva fase
3. En P1→P2: aplica `p2_risk_factor` (0.5×)
4. En P2→Funded: cambia a `funded_mode` config (risk 1.4%, DC 1.5%)

### 6.6 Decorrelacion Temporal

Las cuentas se compran **escalonadas ~7 dias** entre si:
- Semana 1: Cuenta 1
- Semana 2: Cuenta 2
- Semana 3: Cuenta 3
- Semana 4: Cuenta 4

Esto reduce la correlacion entre resultados: si hay un crash el dia 5, una cuenta puede tener buffer y otra no.

### 6.7 Frecuencia de Trading

~18 trades/mes para el deck completo (mediana 17, rango 12-26).

Top generadores: VMR_USDCHF_H1 (~4/mes), EMArib_XNGUSD_loose_H4 (~4/mes).
Menos activos: SwBrk_SPY_slow_H4 (~1/10 meses), VMR_SPY_H4 (~1/5 meses).

En un examen de 30 dias: ~15-20 trades por cuenta. Suficiente para el target del 10% a 2% risk.

### 6.3 Dos Modos de Operacion

| Aspecto | Exam (P1/P2) | Funded |
|---------|-------------|--------|
| Objetivo | Pasar rapido | Sobrevivir largo |
| Risk/trade | 2.0% | 1.4% |
| Daily cap | 2.5% | 1.5% |
| P2 factor | 0.5× | N/A |
| Comportamiento | Agresivo | Conservador |

La transicion es **automatica**: cuando la cuenta alcanza el target + 4 dias de trading, el sistema cambia de modo y actualiza el YAML.

### 6.4 Decorrelacion Temporal

Las cuentas se compran **escalonadas ~7 dias** entre si:
- Semana 1: Cuenta 1
- Semana 2: Cuenta 2
- Semana 3: Cuenta 3
- Semana 4: Cuenta 4

Esto reduce la correlacion entre resultados: si hay un crash el dia 5, una cuenta puede tener buffer y otra no.

---

## 7. ROI Esperado

### 7.1 Parametros

| Parametro | Valor |
|-----------|-------|
| Funded rate | ~28.6% OOS |
| Coste examen ($5K) | EUR 40 (reembolsado al fondear) |
| Supervivencia media | ~6 meses (funded mode) |
| Ingreso mensual / $5K funded | ~EUR 300 net (80% split) |

### 7.2 Escenarios (Estado Estacionario)

| Examenes/mes | Coste | Nuevas funded | Activas (SS) | Ingreso | Neto/mes |
|-------------|-------|---------------|-------------|---------|----------|
| 5 | EUR 200 | 1.4 | 8.6 | EUR 2,571 | EUR +2,371 |
| 10 | EUR 400 | 2.9 | 17.1 | EUR 5,143 | EUR +4,743 |
| 20 | EUR 800 | 5.7 | 34.3 | EUR 10,286 | EUR +9,486 |

*Estado estacionario = cuentas activas = (nuevas/mes) × (supervivencia media)*

### 7.3 Worst Case (Todo a la Mitad)

| Examenes/mes | Neto/mes |
|-------------|----------|
| 10 | ~EUR +250 |
| 20 | ~EUR +700 |

---

## 8. Setup y Uso

### 8.1 Requisitos

```
Python 3.10+
MetaTrader 5 Terminal (Windows)
pip install MetaTrader5 PyYAML pandas numpy
```

### 8.2 Backtest / Optimizacion

```bash
# Optimizar deck y controles
python -X utf8 scripts/optimize_deck.py

# Validacion independiente
python -X utf8 scripts/production_sim.py
```

### 8.3 Trading en Vivo

1. Rellenar `config/accounts.yaml` con credenciales MT5
2. Ajustar `symbol_map` segun los nombres del broker
3. Abrir MT5 Terminal en Windows

```bash
# Test sin ordenes reales
python -X utf8 scripts/live_trader.py --dry-run

# Un solo ciclo (debug)
python -X utf8 scripts/live_trader.py --once

# Trading real
python -X utf8 scripts/live_trader.py
```

### 8.4 Validacion Live vs Backtest

El sistema incluye un validador que compara los trades reales contra lo que el backtest habria hecho en el mismo periodo:

```bash
# Validar todos los trades del log
python -X utf8 scripts/validate_live.py

# Solo ultimos 7 dias
python -X utf8 scripts/validate_live.py --days 7

# Usando datos descargados de MT5 (en vez de historicos locales)
python -X utf8 scripts/validate_live.py --from-mt5
```

El validador reporta:
- **Matched & correct**: trade live coincide con backtest (direccion + SL)
- **MISMATCH**: mismo trade pero direccion o SL distintos → **BUG real, investigar**
- **Live only**: live tomo un trade que el backtest no → timing o controles de portfolio
- **BT only**: backtest habria tomado un trade que live no → controles, offline, has_position

**Objetivo: 0 MISMATCHES.** Diferencias en Live-only/BT-only son esperables por controles de portfolio per-account.

### 8.5 Monitorizacion

- **Logs**: `data/live_trades.log` (consola + archivo)
- **Estado**: `data/live_state.json` (equity, DD, trades por cuenta)
- **Historial**: `data/trade_history.jsonl` (cada trade con ticket MT5)
- **Config**: `config/accounts.yaml` (estado actualizado automaticamente)

---

## Notas Importantes

- **Fidelidad backtest-live**: El sistema live replica exactamente el motor de backtest: pending signal pattern, gap adjustment, anti-martingala, RiskManager sizing, has_position tracking. Cada componente del backtest tiene su equivalente en live.
- **Timezone**: Darwinex y FTMO usan GMT+2/+3 (EET). Las velas H4/H1 se forman a las mismas horas.
- **Symbol mapping**: SPY en Darwinex puede ser US500 en FTMO. XTIUSD puede ser USOIL. Verificar antes de ir live.
- **Spreads**: Nuestro backtest es conservador (spreads reales de Darwinex son mas amplios que FTMO en la mayoria de pares). FTMO tiene raw spreads + $6/lot comision.
- **Comisiones**: Usamos $7/lot vs $6/lot real de FTMO → margen de seguridad.
- **Pending signal**: Las señales se generan en la vela completada (bar[i]), se ejecutan en la apertura de la siguiente (bar[i+1]). Esto elimina look-ahead bias tanto en backtest como en live.
- **Datos**: En backtest se usan datos historicos de Darwinex (M1 con spread real). En live se descargan datos directamente del broker MT5 conectado. Darwinex no interviene en live.
- **SL/TP holgados**: Los SL son 1.0-3.0 ATR (tipicamente 30-150 pips). La diferencia de precio entre brokers (~0.5 pips) es <1% del SL, por lo que usar datos de un broker para generar señales ejecutadas en otro es viable.
