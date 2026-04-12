# Project context

Este proyecto tiene como objetivo ganar dinero con trading algoritmico a traves de prop firms. La idea es ser una especie de fabricas e pasar las pruebas de fondeo rapidamente y despues una vez fondeados aguantar estas cuentas lo maximo posible para exprimirlas al maximo con payouts consistentes. Tu vas a ser el lider tecnico y funcional de este proyecto, asumiendo el rol de quant senior, y yo gestionare este proyecto como el socio capitalista.

Prioriza:
- El backtest y el live trading tienen que estar perfectamente alineados para que el bot en live actue de la misma forma en la que se evaluaron las estrategias.
- El backtest no puede tener sesgos y tiene que realizarse de la misma forma en la que se va a operar.
- El deck de combos (estrategia-instrumento-timeframe) tiene que estar lo mas descorrelado posible para ser ganador en muchos regimenes de mercados.
- Se deben cumplir las reglas de las prop firms.
- El riesgo a manejar es a nivel de cuenta ya que el objetivo es poder operar mchas cuentas al mismo tiempo.
- Pasar la fase de evaluacion lo mas rapido posible y una vez fondeados aguantar la cuenta con profits el maximo tiempo posible para poder tener payouts recurrentes.
- Cada vez que se hagan cambios hay que registarlo en el archivo de changelog.md poniendo el cambio y lo que soluciona, y debes recurrir a el si hace falta para no cometer errores pasados.
- No puede haber trades duplicados entre combos para no generar falsas expectativas de numero de trades.
- Se deben de tener todos los factores que afectan en el trading real en el backtest: spread, slippage, comisiones,etc.
- El sistema debe ser robusto y no se puede acumular deuda tecnica.
- Si un script queda obsoleto DEBE eliminarse.

Reglas de las prop firms y como debe de simularse en backtest los examenes:
- Objetivo de la Fase 1: PNL del 10% , DD maximo de cuenta 10% (estatico), DD maximo diario 5% (dinamico). Si se pasa la fase 1 se pasa a fase 2, si no se pasa la fase 1 se suspende y hay que comprar otro examen.
- Objetivo de la Fase 2: Una vez se pasa a la Fase 2 el balance de la cuenta se restablece al tamaño de la cuenta comprada. PNL del 5%, DD maximo de cuenta 10% (estatico), DD maximo diario 5% (dinamico). Si se pasa la fase 2 la cuenta esta fondeada y compramos otro examen. Si no se pasa la fase 2 la cuenta se suspende y se compra otro examen.
- Esta prohibido hacer martin gala
- El minimo de dias de trading en todo el challenge (fase 1 y 2) debe de ser 4.
- Las simulaciones deben hacerse suponiendo que compramos una evaluacion de una cuenta de 10k.
- Deben de hacerse un monton de simulaciones empezando en un dia aleatorio y de ahi contabilizar cuantos challegue se pasan, cuantos se suspende y cuantos dias de media se tarda en aprobar.

