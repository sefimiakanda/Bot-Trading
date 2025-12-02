import yfinance as yf
import pandas as pd
import ta
import smtplib
import csv
import os
import feedparser
import time
from email.mime.text import MIMEText
from datetime import datetime
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# ==========================================
# ZONE DE CONFIGURATION (À MODIFIER PAR TOI)
# ==========================================

# 1. PARAMÈTRES DU MARCHÉ
SYMBOL = "EURUSD=X"       # Symbole Yahoo (ex: GBPUSD=X, BTC-USD, GOLD)
TIMEFRAME = "1h"          # Intervalle (1h, 1d, 30m)
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# 2. PARAMÈTRES EMAIL (GMAIL)
EMAIL_SENDER = "sefimiakanda@gmail.com"        # Ton adresse Gmail
EMAIL_PASSWORD = "nboo bwmi meim lqia"     # Ton mot de passe d'application (pas le mdp normal)
EMAIL_RECEIVER = "sefimiakanda@gmail.com" # L'email qui reçoit l'alerte

# 3. FICHIER DE LOG
CSV_FILE = "trading_journal.csv"

# 4. RÉGLAGE DE LA SENSIBILITÉ
SENTIMENT_WEIGHT = 2.0    # Importance du sentiment (x2 est équilibré)
DECISION_THRESHOLD = 3.0  # Score nécessaire pour déclencher une alerte (3 est strict)

# 5. PARAMÈTRES D'EXÉCUTION ET DE GESTION DU RISQUE
EXECUTION_INTERVAL_MIN = 30 # Pause entre les analyses (en minutes). Doit correspondre à TIMEFRAME
SL_TP_MULTIPLIER = 1.5      # Multiplicateur de l'ATR pour définir le SL/TP

# ==========================================
# FIN DE LA CONFIGURATION
# ==========================================

def get_market_data(symbol, period="1mo", interval="1h"):
    """Télécharge les données OHLC via Yahoo Finance"""
    print(f"\n[1/4] Téléchargement des données pour {symbol}...")
    try:
        df = yf.download(symbol, period=period, interval=interval, progress=False)
        if df.empty:
            print("Erreur: Aucune donnée reçue.")
            return None
        # Gestion multi-index si nécessaire
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        print(f"Erreur de connexion Yahoo: {e}")
        return None

def calculate_technicals(df):
    """Calcule tous les indicateurs techniques"""
    print("[2/4] Calcul des indicateurs techniques...")
    
    # EMA
    df['EMA20'] = ta.trend.EMAIndicator(close=df['Close'], window=20).ema_indicator()
    df['EMA50'] = ta.trend.EMAIndicator(close=df['Close'], window=50).ema_indicator()
    
    # MACD
    macd = ta.trend.MACD(close=df['Close'], window_slow=MACD_SLOW, window_fast=MACD_FAST, window_sign=MACD_SIGNAL)
    df['MACD'] = macd.macd()
    df['MACD_Signal'] = macd.macd_signal()
    
    # RSI
    df['RSI'] = ta.momentum.RSIIndicator(close=df['Close'], window=RSI_PERIOD).rsi()
    
    # ATR (Volatilité)
    df['ATR'] = ta.volatility.AverageTrueRange(high=df['High'], low=df['Low'], close=df['Close'], window=14).average_true_range()
    
    # Breakouts (Plus haut/bas des 20 dernières bougies)
    df['20_High'] = df['High'].rolling(window=20).max()
    df['20_Low'] = df['Low'].rolling(window=20).min()
    
    return df

def get_sentiment_analysis(symbol_name="EURUSD"):
    """Analyse les news RSS avec VADER"""
    print("[3/4] Analyse du sentiment (Google News + VADER)...")
    
    # Construction de l'URL de recherche RSS Google News
    query = f"{symbol_name} forex trading market news"
    rss_url = f"https://news.google.com/rss/search?q={query.replace(' ', '+')}&hl=en-US&gl=US&ceid=US:en"
    
    try:
        feed = feedparser.parse(rss_url)
        analyzer = SentimentIntensityAnalyzer()
        sentiment_scores = []
        
        # Analyse des 10 premiers titres
        for entry in feed.entries[:10]:
            vs = analyzer.polarity_scores(entry.title)
            score = vs['compound']
            if score != 0: # On ignore les titres purement neutres
                sentiment_scores.append(score)
        
        if sentiment_scores:
            avg_sentiment = sum(sentiment_scores) / len(sentiment_scores)
        else:
            avg_sentiment = 0.0
            
        return avg_sentiment
    except Exception as e:
        print(f"Erreur sentiment: {e}")
        return 0.0

def analyze_market_logic(df, sentiment_score):
    """Combine Technique + Sentiment pour le score final et calcule SL/TP"""
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    tech_score = 0
    reasons = []
    
    # ... (Le calcul du tech_score reste exactement le même) ...
    # 1. Tendance EMA
    if last['EMA20'] > last['EMA50']: tech_score += 1
    else: tech_score -= 1
        
    # 2. RSI
    if last['RSI'] < 30:
        tech_score += 1
        reasons.append(f"RSI Oversold ({last['RSI']:.1f})")
    elif last['RSI'] > 70:
        tech_score -= 1
        reasons.append(f"RSI Overbought ({last['RSI']:.1f})")
        
    # 3. MACD
    if last['MACD'] > last['MACD_Signal']: tech_score += 1
    else: tech_score -= 1
        
    # 4. Breakouts
    if last['Close'] > prev['20_High']:
        tech_score += 2
        reasons.append("Breakout High 20")
    elif last['Close'] < prev['20_Low']:
        tech_score -= 2
        reasons.append("Breakout Low 20")
        
    # SCORE FINAL
    weighted_sentiment = sentiment_score * SENTIMENT_WEIGHT
    final_score = tech_score + weighted_sentiment
    
    signal = "NEUTRAL"
    stop_loss = None
    take_profit = None
    
    # --- CALCUL DES NIVEAUX DE TRADING ---
    
    # La volatilité est basée sur la dernière valeur ATR (Average True Range)
    volatility = last['ATR'] * SL_TP_MULTIPLIER
    entry_price = last['Close']
    
    if final_score >= DECISION_THRESHOLD:
        signal = "BUY"
        # Pour l'achat: SL en dessous du prix d'entrée, TP au-dessus
        stop_loss = entry_price - volatility
        take_profit = entry_price + volatility
        
    elif final_score <= -DECISION_THRESHOLD:
        signal = "SELL"
        # Pour la vente: SL au-dessus du prix d'entrée, TP en dessous
        stop_loss = entry_price + volatility
        take_profit = entry_price - volatility
        
    # Renvoyer les 5 informations nécessaires (y compris SL/TP)
    return signal, final_score, reasons, entry_price, stop_loss, take_profit

def send_email(subject, body):
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = EMAIL_SENDER
    msg['To'] = EMAIL_RECEIVER

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp_server:
            smtp_server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            smtp_server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print(">> Email d'alerte envoyé !")
    except Exception as e:
        print(f">> Erreur envoi email: {e}")

def log_to_csv(data):
    file_exists = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=data.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(data)
    print(f">> Données sauvegardées dans {CSV_FILE}")

def main():
    print("--- DÉMARRAGE DU BOT FOREX ---")
    
    # 1. Données
    df = get_market_data(SYMBOL, interval=TIMEFRAME)
    if df is None: return

    # 2. Technique
    df = calculate_technicals(df)

    # 3. Sentiment
    sentiment_score = get_sentiment_analysis(SYMBOL)

    # 4. Décision
    signal, score, reasons, price, sl, tp = analyze_market_logic(df, sentiment_score)
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Affichage Console
    print("\n" + "="*30)
    print(f"RÉSULTATS POUR {SYMBOL} à {timestamp}")
    print(f"Prix actuel : {price:.5f}")
    print(f"Sentiment   : {sentiment_score:.4f} (Echelle -1 à 1)")
    print(f"Score Total : {score:.2f} (Seuil: +/- {DECISION_THRESHOLD})")
    print(f"SIGNAL      : {signal}")
    print(f"Raisons     : {', '.join(reasons) if reasons else 'Aucune divergence majeure'}")
    print("="*30 + "\n")

    # 5. Alerte Email (Seulement si BUY ou SELL)
    if signal != "NEUTRAL":
        subject = f"ALERTE {signal} : {SYMBOL} (Score {score:.2f})"
        body = f"""
        Bot Trading Alert
        -----------------
        Symbole : {SYMBOL}
        Signal  : {signal}
        Prix    : {price:.5f}
        
        Score Technique + Sentiment : {score:.2f}
        Sentiment Actuel : {sentiment_score:.4f}
        
        Détails Techniques :
        {', '.join(reasons)}
        
        ATR (Volatilité) : {df.iloc[-1]['ATR']:.5f}
        """
        send_email(subject, body)
    
    # 6. Sauvegarde CSV
    log_to_csv({
        "Date": timestamp,
        "Symbol": SYMBOL,
        "Price": price,
        "Signal": signal,
        "Score": score,
        "Sentiment": sentiment_score,
        "RSI": df.iloc[-1]['RSI'],
        "EMA20": df.iloc[-1]['EMA20'],
        "EMA50": df.iloc[-1]['EMA50']
    })

def main_loop():
    while True:
        try:
            print(f"\n{'='*50}\nNOUVELLE ANALYSE DÉMARRÉE : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            # 1. Données
            # Nous ne téléchargeons qu'une courte période pour plus de rapidité,
            # car le bot est appelé fréquemment.
            df = get_market_data(SYMBOL, period="2d", interval=TIMEFRAME)
            if df is None:
                time.sleep(60) # Attendre une minute en cas d'erreur de données
                continue
        
            # 2. Technique
            df = calculate_technicals(df)

            # 3. Sentiment
            sentiment_score = get_sentiment_analysis(SYMBOL)

            # 4. Décision (SL et TP inclus)
            signal, score, reasons, price, sl, tp = analyze_market_logic(df, sentiment_score)
            
            # Affichage Console
            print("\n" + "="*30)
            print(f"RÉSULTATS POUR {SYMBOL}")
            print(f"Prix d'Entrée : {price:.5f}")
            print(f"Score Total   : {score:.2f}")
            print(f"SIGNAL        : {signal}")
            
            if signal != "NEUTRAL":
                print(f"  SL calculé : {sl:.5f}")
                print(f"  TP calculé : {tp:.5f}")
                
                # 5. Alerte Email
                subject = f"ALERTE {signal} : {SYMBOL} (Score {score:.2f})"
                body = f"""
                🚨 ALERTE TRADING EN TEMPS RÉEL
                -----------------
                Symbole : {SYMBOL}
                Signal  : {signal}
                
                Niveaux Recommandés :
                Prix d'Entrée : {price:.5f}
                Stop Loss (SL): {sl:.5f}
                Take Profit (TP): {tp:.5f}
                
                Score Combné : {score:.2f}
                Sentiment : {sentiment_score:.4f}
                """
                send_email(subject, body)
                
                # 6. Sauvegarde CSV (avec les niveaux SL/TP)
                log_to_csv({
                    "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "Symbol": SYMBOL,
                    "Signal": signal,
                    "EntryPrice": price,
                    "StopLoss": sl,
                    "TakeProfit": tp,
                    "Score": score,
                    "Sentiment": sentiment_score,
                })
            else:
                print("Pas de signal fort. Prochaine analyse bientôt.")
            
            print("="*1)
            
        except Exception as e:
            print(f"Une erreur majeure est survenue dans la boucle: {e}")
            
        # Pause avant la prochaine exécution
        wait_seconds = EXECUTION_INTERVAL_MIN * 1
        print(f"\nPause de {EXECUTION_INTERVAL_MIN} minutes...")
        time.sleep(wait_seconds)


if __name__ == "__main__":
    main_loop()