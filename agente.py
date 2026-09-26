"""
Agente de trading swing para Alpaca + ChatGPT (OpenAI).
- Las reglas de compra/venta están en código (predecibles y auditables).
- La IA solo revisa noticias recientes y puede VETAR una compra.
- Cada compra deja un stop-loss vivo en el bróker (se ejecuta aunque el agente esté apagado).
"""
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
from openai import OpenAI
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass, QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

# ======================= CONFIGURACIÓN =======================
# ETFs de precio moderado (con $200 solo se compran acciones enteras que quepan).
LISTA = ["SCHD", "SCHG", "SCHB", "VEA", "XLF", "XLE", "XLU", "XLV"]

POSICION_PCT = 0.33          # % del capital por posición
MAX_POSICIONES = 3           # posiciones abiertas a la vez
MAX_POR_ORDEN = 100.0        # tope en dólares por orden
MAX_COMPRAS_POR_DIA = 2      # tope de compras diarias
STOP_PCT = 0.07              # stop-loss en el bróker: -7%
MAX_SOBRE_SMA20 = 0.05       # no comprar si el precio está >5% sobre su media de 20 días
CAPITAL_INICIAL = 200.0      # referencia para el freno de emergencia
PERDIDA_MAXIMA = 0.20        # si el capital cae 20%, no compra más

MODO = os.getenv("MODO", "paper").lower()          # "paper" o "real"
SOLO_ANALISIS = os.getenv("SOLO_ANALISIS", "false").lower() == "true"
MODELO_IA = os.getenv("MODELO_IA", "gpt-6-luna")
# =============================================================

KEY = os.environ["ALPACA_API_KEY"]
SECRET = os.environ["ALPACA_SECRET_KEY"]
trading = TradingClient(KEY, SECRET, paper=(MODO != "real"))
datos = StockHistoricalDataClient(KEY, SECRET)
ia = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

resumen = []


def log(msg):
    print(msg)
    resumen.append(msg)


def sma(valores, n):
    return sum(valores[-n:]) / n if len(valores) >= n else None


def obtener_cierres(simbolos):
    req = StockBarsRequest(
        symbol_or_symbols=simbolos,
        timeframe=TimeFrame.Day,
        start=datetime.now(timezone.utc) - timedelta(days=120),
        feed=DataFeed.IEX,  # datos gratuitos
    )
    df = datos.get_stock_bars(req).df
    cierres = {}
    for s in simbolos:
        if s in df.index.get_level_values(0):
            cierres[s] = df.loc[s]["close"].tolist()
    return cierres


def noticias(simbolo, limite=5):
    r = requests.get(
        "https://data.alpaca.markets/v1beta1/news",
        params={"symbols": simbolo, "limit": limite},
        headers={"APCA-API-KEY-ID": KEY, "APCA-API-SECRET-KEY": SECRET},
        timeout=15,
    )
    r.raise_for_status()
    return [n.get("headline", "") for n in r.json().get("news", [])]


def revision_ia(simbolo, titulares):
    """Devuelve (aprobado: bool, motivo: str). Si la IA falla, NO se compra."""
    if not titulares:
        return True, "sin noticias recientes"
    prompt = (
        f"Eres un filtro de riesgo. Estas son noticias recientes sobre {simbolo}:\n"
        + "\n".join(f"- {t}" for t in titulares)
        + "\n\nResponde SOLO con JSON: {\"decision\": \"comprar\" o \"evitar\", \"motivo\": \"frase corta en español\"}. "
        "Usa \"evitar\" solo ante noticias claramente graves (fraude, quiebra, demanda mayor, "
        "caída fuerte de resultados, evento extraordinario). Si son normales, \"comprar\"."
    )
    try:
        resp = ia.responses.create(model=MODELO_IA, reasoning={"effort": "low"}, input=prompt)
        texto = resp.output_text
        texto = texto.strip().removeprefix("```json").removesuffix("```").strip()
        data = json.loads(texto)
        return data.get("decision") == "comprar", data.get("motivo", "")
    except Exception as e:
        return False, f"error de IA ({e}); por seguridad no se compra"


def cancelar_ordenes(simbolo):
    abiertas = trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[simbolo], nested=True))
    for o in abiertas:
        trading.cancel_order_by_id(o.id)


def main():
    reloj = trading.get_clock()
    if not reloj.is_open and not SOLO_ANALISIS:
        log("Mercado cerrado. Nada que hacer.")
        return

    cuenta = trading.get_account()
    capital, efectivo = float(cuenta.equity), float(cuenta.cash)
    posiciones = {p.symbol: p for p in trading.get_all_positions()}
    log(f"Modo: {MODO.upper()}{' (solo análisis)' if SOLO_ANALISIS else ''} | Capital: ${capital:.2f} | Efectivo: ${efectivo:.2f}")

    cierres = obtener_cierres(sorted(set(LISTA) | set(posiciones)))

    # ---------- SALIDAS: vender si el precio pierde su media de 50 días ----------
    for s, pos in posiciones.items():
        c = cierres.get(s)
        if not c or sma(c, 50) is None:
            continue
        precio, m50 = c[-1], sma(c, 50)
        if precio < m50:
            log(f"VENDER {s}: precio {precio:.2f} < media50 {m50:.2f} (P/G: ${float(pos.unrealized_pl):.2f})")
            if not SOLO_ANALISIS:
                cancelar_ordenes(s)
                trading.close_position(s)

    # ---------- FRENOS DE SEGURIDAD ----------
    if capital < CAPITAL_INICIAL * (1 - PERDIDA_MAXIMA):
        log(f"FRENO: capital bajo ${CAPITAL_INICIAL*(1-PERDIDA_MAXIMA):.2f}. Sin compras nuevas. Revisa la estrategia.")
        return
    hoy = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    compras_hoy = [o for o in trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.ALL, after=hoy, side=OrderSide.BUY))]
    cupos = min(MAX_POSICIONES - len(trading.get_all_positions()), MAX_COMPRAS_POR_DIA - len(compras_hoy))
    if cupos <= 0:
        log("Sin cupos para nuevas compras hoy.")
        return

    # ---------- ENTRADAS: tendencia alcista sin estar sobreextendido ----------
    candidatos = []
    for s in LISTA:
        c = cierres.get(s)
        if s in posiciones or not c or sma(c, 50) is None:
            continue
        precio, m20, m50 = c[-1], sma(c, 20), sma(c, 50)
        if precio > m20 > m50 and precio <= m20 * (1 + MAX_SOBRE_SMA20):
            candidatos.append((precio / m50 - 1, s, precio))
    candidatos.sort(reverse=True)
    if not candidatos:
        log("Ninguna señal de compra hoy.")

    for _, s, precio in candidatos:
        if cupos <= 0:
            break
        monto = min(capital * POSICION_PCT, MAX_POR_ORDEN, efectivo)
        qty = math.floor(monto / precio)
        if qty < 1:
            log(f"{s}: señal, pero ${monto:.2f} no alcanza para 1 acción (${precio:.2f}).")
            continue
        ok, motivo = revision_ia(s, noticias(s))
        if not ok:
            log(f"{s}: IA vetó la compra -> {motivo}")
            continue
        stop = round(precio * (1 - STOP_PCT), 2)
        log(f"COMPRAR {qty} {s} a ~${precio:.2f} | stop-loss ${stop} | IA: {motivo}")
        if not SOLO_ANALISIS:
            trading.submit_order(MarketOrderRequest(
                symbol=s, qty=qty, side=OrderSide.BUY, time_in_force=TimeInForce.GTC,
                order_class=OrderClass.OTO, stop_loss=StopLossRequest(stop_price=stop),
            ))
        efectivo -= qty * precio
        cupos -= 1


if __name__ == "__main__":
    try:
        main()
    finally:
        ruta = os.getenv("GITHUB_STEP_SUMMARY")
        if ruta:
            with open(ruta, "a") as f:
                f.write("## Resumen del agente\n\n" + "\n".join(f"- {l}" for l in resumen) + "\n")
