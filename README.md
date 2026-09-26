# Agente de trading (Alpaca + ChatGPT)

Agente de swing trading que corre en GitHub Actions dos veces por día hábil.
Empieza en **paper trading** (dinero de práctica).

## Cómo decide
- **Compra** ETFs cuya tendencia es alcista (precio > media 20 días > media 50 días) sin estar sobreextendidos.
- Antes de comprar, **ChatGPT (gpt-6-luna)** lee las noticias recientes y puede vetar la compra.
- Cada compra deja un **stop-loss de -7%** vivo en Alpaca.
- **Vende** si el precio cae por debajo de su media de 50 días.
- Frenos: máximo 3 posiciones, 2 compras por día, $100 por orden, y sin compras si el capital cae 20%.

## Puesta en marcha
1. Crea un repositorio **privado** en GitHub y sube estos archivos (incluida la carpeta `.github`).
2. En Alpaca (paper), ve a *Account → Reset* y pon el saldo en **$200** para que la práctica sea realista.
3. En GitHub: *Settings → Secrets and variables → Actions → New repository secret* y crea:
   - `ALPACA_API_KEY`
   - `ALPACA_SECRET_KEY`
   - `OPENAI_API_KEY` (créala en platform.openai.com)
4. Ve a la pestaña *Actions*, elige "Agente de trading" → *Run workflow* con "solo analizar" en `true`. Revisa el resumen.
5. Si todo sale bien, deja que corra solo según el horario.

## Ajustes
Todo se cambia en el bloque CONFIGURACIÓN de `agente.py` (lista de ETFs, stop, tamaños).

## Pasar a dinero real (después de 1–2 meses de práctica)
Cambia `MODO: paper` a `MODO: real` en `.github/workflows/agente.yml` y usa tus claves **reales** de Alpaca en los secrets.
Hazlo solo si los resultados en paper fueron consistentes.
