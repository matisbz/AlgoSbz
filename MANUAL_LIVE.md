# Manual de Usuario — AlgoSbz Live Trading

## Requisitos Previos

### Software
1. **Windows 10/11** (MT5 solo corre en Windows)
2. **MetaTrader 5** instalado y funcionando
3. **Python 3.10+** con las dependencias:
   ```bash
   pip install MetaTrader5 pandas numpy pyyaml
   ```

### Cuentas
- 1 a 4 cuentas de prop firm (FTMO, The Funded Trader, etc.)
- Credenciales MT5 de cada cuenta (login, password, server)
- Cuentas de $100,000 recomendadas (ajustar `initial_balance` si es diferente)

---

## Paso 1: Configurar Cuentas

Editar `config/accounts.yaml`:

```yaml
accounts:
  - name: "FTMO_1"
    enabled: true
    login: 12345678          # ← Tu numero de cuenta MT5
    password: "tu_password"  # ← Tu password MT5
    server: "FTMO-Demo2"    # ← Nombre exacto del servidor (ver MT5 → File → Login)
    start_date: "2026-03-24" # ← Fecha de compra del examen
    state: phase1            # ← Dejar en phase1 al empezar
    initial_balance: 100000  # ← Balance inicial de la cuenta

  - name: "TFT_1"
    enabled: true
    login: 87654321
    password: "otro_password"
    server: "TheFundedTrader-Server"
    start_date: "2026-03-31"  # ← Comprar ~7 dias despues
    state: phase1
    initial_balance: 100000
```

**Importante**: Las cuentas deshabilitadas (`enabled: false`) o con `login: 0` se ignoran.

### Verificar Symbol Map

El symbol map traduce los nombres internos del sistema a los nombres de tu broker. **Esto es critico**.

Para verificar los nombres en tu broker:
1. Abre MT5
2. Ve a **View → Market Watch** (Ctrl+M)
3. Click derecho → **Show All**
4. Busca cada simbolo y anota el nombre exacto

Los mas comunes que cambian entre brokers:

| Interno | FTMO | MyForexFunds | The Funded Trader |
|---------|------|--------------|-------------------|
| XAUUSD | XAUUSD | GOLD | XAUUSD |
| XTIUSD | XTIUSD | USOIL | WTI |
| XNGUSD | XNGUSD | NATGAS | NGAS |
| SPY | US500 | SPX500 | US500 |

Editar en `config/accounts.yaml`:
```yaml
symbol_map:
  EURUSD: "EURUSD"     # Cambiar si tu broker usa otro nombre
  GBPJPY: "GBPJPY"
  USDCHF: "USDCHF"
  XAUUSD: "XAUUSD"     # ← GOLD, GOLDm, etc.
  XTIUSD: "XTIUSD"     # ← USOIL, WTI, CrudeOIL
  XNGUSD: "XNGUSD"     # ← NATGAS, NGAS
  SPY: "US500"          # ← SPX500, US500
```

---

## Paso 2: Preparar MetaTrader 5

1. **Abrir MT5** y loguearte en cualquiera de tus cuentas
2. **Habilitar Algo Trading**:
   - Menu → Tools → Options → Expert Advisors
   - Marcar "Allow Algo Trading"
   - Marcar "Allow DLL imports"
3. **Market Watch**: Asegurar que TODOS los simbolos del deck estan visibles:
   - View → Market Watch → Click derecho → Show All
   - O buscar manualmente: EURUSD, GBPJPY, USDCHF, XAUUSD, XTIUSD, XNGUSD, US500
4. **Dejar MT5 abierto** — el sistema necesita la terminal corriendo

**MT5 debe estar abierto 24/5** (lunes a viernes). El sistema se conecta a traves del terminal.

---

## Paso 3: Test en Modo Dry-Run

**SIEMPRE probar primero en dry-run antes de operar con dinero real.**

```bash
python -X utf8 scripts/live_trader.py --dry-run
```

Esto hace todo excepto enviar ordenes reales a MT5:
- Descarga datos historicos
- Genera senales
- Evalua riesgo
- Log de todo lo que HARIA

Dejar corriendo al menos 24-48h y verificar en los logs que:
- Se conecta correctamente a cada cuenta
- Detecta nuevas barras
- Genera senales coherentes
- Los tamanios de posicion son razonables (~0.1-0.5 lots en cuentas 100k)

### Verificar Logs

Los logs se guardan en `data/live_trades.log`:
```bash
# Ver ultimas lineas del log
tail -f data/live_trades.log
```

Buscar estos mensajes clave:
- `Connected: 12345@FTMO-Demo` — conexion OK
- `NEW BAR: EURUSD H4 @ 2026-03-24 16:00` — deteccion de barras
- `PENDING SIGNAL: VMR_USDCHF_H1 → ENTER_LONG` — senal generada
- `[DRY RUN] Account_1 BUY VMR_USDCHF_H1 0.15 lots` — orden simulada
- `Cycle 100 — no new bars` — heartbeat normal

### Verificar con el Validador

Despues de 1+ semana de dry-run:
```bash
python -X utf8 scripts/validate_live.py --offline
```

---

## Paso 4: Lanzar en LIVE

Una vez satisfecho con el dry-run:

```bash
python -X utf8 scripts/live_trader.py
```

El sistema:
1. Se conecta a MT5
2. Descarga 500 barras de historia (segundos)
3. Inicializa estrategias
4. Entra en loop de trading (cada 30 segundos):
   - Detecta nuevas barras completadas
   - Genera senales
   - Ejecuta ordenes via MT5
   - Sincroniza posiciones cerradas (SL/TP)

### Lo que Hace Automaticamente

- **Gestion de fases**: P1 (10% target) → P2 (5% target) → Funded
- **Risk management**: Anti-martingale, daily cap, cooldown por combo
- **Rotacion de cuentas**: Login/logout automatico para ver posiciones de cada cuenta
- **Persistencia**: Estado guardado en `data/live_state.json` (sobrevive a reinicios)
- **Trade log**: Cada operacion en `data/trade_history.jsonl`

### Lo que NO Hace

- **No** compra examenes automaticamente
- **No** gestiona depositos/retiros
- **No** notifica por Telegram/email (puedes anadir esto)
- **No** cierra posiciones manualmente (solo abre con SL/TP, el broker cierra)

---

## Paso 5: Monitoreo Diario

### Checklist Diaria (1 minuto)

1. Verificar que MT5 esta abierto y conectado
2. Verificar que el script sigue corriendo:
   ```bash
   # Si lo lanzaste en background
   tail -5 data/live_trades.log
   ```
3. Revisar en MT5 que las posiciones tienen SL/TP correctos

### Checklist Semanal (5 minutos)

1. Ejecutar el validador:
   ```bash
   python -X utf8 scripts/validate_live.py --days 7
   ```
2. Revisar el estado de cada cuenta:
   ```bash
   cat data/live_state.json
   ```
3. Verificar en la web de la prop firm que los numeros coinciden

---

## Escenarios Comunes

### "El script se detuvo / reinicio el PC"

No pasa nada. El sistema restaura estado automaticamente:
```bash
python -X utf8 scripts/live_trader.py
```
- Posiciones abiertas en MT5 siguen vivas (SL/TP son server-side)
- El sistema las detecta y retoma el tracking
- Pending signals se restauran de `data/live_state.json`

### "Una cuenta paso Phase 1"

El sistema detecta automaticamente cuando profit >= 10% y trading_days >= 4.
- Actualiza `config/accounts.yaml` a `state: phase2`
- Resetea contadores
- Reduce risk (P2 usa 50% del risk de P1)
- Log: `>>> [FTMO_1] PHASE TRANSITION → phase2 <<<`

**TU**: Debes confirmar en la web de la prop firm que realmente paso y obtener las nuevas credenciales de Phase 2. Actualizar login/password en accounts.yaml si cambian.

### "Una cuenta paso Phase 2 → Funded"

Mismo proceso. El sistema cambia a `funded_mode` (1.4% risk, caps mas conservadores).

**TU**: Obtener credenciales de la cuenta funded real.

### "Quiero anadir una cuenta nueva"

1. Editar `config/accounts.yaml`
2. Anadir nueva entrada con `enabled: true` y credenciales
3. Reiniciar el script

### "El mercado esta cerrado (fin de semana)"

El script sigue corriendo pero no genera senales ni intenta operar. Los pending signals se descartan si son stale (pasaron mas de 1 barra sin ejecutarse).

### "MT5 se desconecto"

El sistema reintenta cada 30 segundos. Pending signals NO se pierden — se ejecutaran cuando MT5 reconecte (si aun son validas). Ordenes con error transient (requote, price changed) se reintentan hasta 3 veces.

### "Quiero parar todo urgentemente"

1. **Ctrl+C** en el terminal del script
2. Las posiciones abiertas en MT5 **siguen abiertas con SL/TP** — no se pierden
3. Para cerrar posiciones manualmente: hacerlo desde MT5 directamente

---

## Estructura de Archivos

```
config/accounts.yaml      ← Tu configuracion (EDITAR)
data/live_state.json      ← Estado persistente (NO TOCAR)
data/trade_history.jsonl   ← Log de trades (para validador)
data/live_trades.log       ← Log del sistema (rotacion automatica, max 50MB)
scripts/live_trader.py     ← Script principal
scripts/validate_live.py   ← Validador live vs backtest
```

---

## Configuracion de Trading (Avanzado)

Solo modificar si sabes lo que haces:

| Parametro | Exam | Funded | Descripcion |
|-----------|------|--------|-------------|
| `risk_per_trade` | 2.0% | 1.4% | Riesgo por operacion |
| `daily_cap_pct` | 2.5% | 1.5% | Stop trading si DD diario >= X% |
| `cooldown` | 1 | 2 | Max perdidas consecutivas por combo/dia |
| `max_instr_per_day` | 2 | 2 | Max trades por instrumento/dia |
| `max_daily_losses` | 3 | 3 | Max perdidas totales/dia |
| `p2_risk_factor` | 0.5 | N/A | Factor de reduccion en Phase 2 |

### Limites FTMO (automaticos, no tocar)

| Limite | Valor | Buffer del sistema |
|--------|-------|--------------------|
| Daily DD | 5% de initial_balance | Para en 4% |
| Total DD | 10% de initial_balance | Para en 9% |
| Min trading days | 4 | Auto-contado |
| P1 target | 10% | Auto-detectado |
| P2 target | 5% | Auto-detectado |

---

## Troubleshooting

### "MT5 login failed"
- Verificar credenciales en accounts.yaml
- Verificar que el servidor es exactamente el que aparece en MT5
- Asegurar que la cuenta no ha expirado

### "No tick for XAUUSD"
- El simbolo no esta en Market Watch
- Abrir MT5 → View → Market Watch → Click derecho → Show All
- Verificar `symbol_map` en accounts.yaml

### "Order failed: Off quotes (code 136)"
- El mercado esta cerrado o el instrumento no opera en este horario
- Normal en fines de semana — el sistema reintenta automaticamente

### "No data for EURUSD H4"
- MT5 no tiene datos suficientes
- Abrir un grafico de ese instrumento en MT5 manualmente para forzar descarga
- Reiniciar el script

### "DAILY CAP HIT"
- El sistema paro de operar porque alcanzo el limite diario
- Normal y esperado — es una proteccion
- Se resetea automaticamente al dia siguiente

### Correr como servicio 24/5

Para que no se pare si cierras la terminal:

**Opcion A: nohup (Linux/WSL)**
```bash
nohup python -X utf8 scripts/live_trader.py > /dev/null 2>&1 &
```

**Opcion B: Task Scheduler (Windows)**
1. Abrir Task Scheduler
2. Create Task → Trigger: At startup
3. Action: Start Program → `python` con argumentos `-X utf8 scripts/live_trader.py`
4. Settings: "If task fails, restart every 1 minute"

**Opcion C: Screen/tmux**
```bash
screen -S algosbz
python -X utf8 scripts/live_trader.py
# Ctrl+A, D para despegar
# screen -r algosbz para reconectar
```
