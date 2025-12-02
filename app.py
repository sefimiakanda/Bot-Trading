import yfinance as yf
import pandas as pd
import ta
import smtplib
import csv
import os
import feedparser
import time # Toujours nécessaire pour le sleep en cas d'erreur
from email.mime.text import MIMEText
from datetime import datetime
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler # Pour l'exécution en arrière-plan

# ==========================================
# ZONE DE CONFIGURATION (À MODIFIER PAR TOI)
# ==========================================

# 1. PARAMÈTRES DU MARCHÉ
SYMBOL = "EURUSD=X"       
TIMEFRAME = "1h"          
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# 2. PARAMÈTRES EMAIL
EMAIL_SENDER = "ton_email@gmail.com"        
EMAIL_PASSWORD = "ton_mot_de_passe_app"     
EMAIL_RECEIVER = "email_destinataire@gmail.com" 

# 3. FICHIER DE LOG
CSV_FILE = "trading_journal.csv"

# 4. RÉGLAGE DE LA SENSIBILITÉ ET SL/TP
SENTIMENT_WEIGHT = 2.0    
DECISION_THRESHOLD = 3.0  
SL_TP_MULTIPLIER = 1.5      # Multiplicateur de l'ATR pour définir le SL/TP

# 5. PARAMÈTRES D'EXÉCUTION (Nouveau)
EXECUTION_INTERVAL_MIN = 60 # Le bot s'exécutera toutes les 60 minutes

# ==========================================
# FONCTIONS TECHNIQUES ET LOGICIELLES (INCHANGÉES)
# ==========================================

# NOTE: Les fonctions get_market_data, calculate_technicals, get_sentiment_analysis, 
# send_email et log_to_csv restent exactement les mêmes que dans le code précédent. 
# Je les omets ici pour la concision, mais elles doivent être présentes dans app.py.

# ----------------------------------------------------------------------------------------------------
# ⚠️ ATTENTION : COLLER ICI LES FONCTIONS get_market_data, calculate_technicals, get_sentiment_analysis, 
#               send_email ET log_to_csv DEPUIS VOTRE FICHIER PRÉCÉDENT 
# ----------------------------------------------------------------------------------------------------
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


# ==========================================
# LOGIQUE D'ANALYSE (MISE À JOUR)
# ==========================================

# NOTE: analyze_market_logic est la seule fonction du cœur du bot qui change,
# car nous lui ajoutons la gestion du SL/TP.

def analyze_market_logic(df, sentiment_score):
    """Combine Technique + Sentiment pour le score final et calcule SL/TP"""
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    tech_score = 0
    reasons = []
    
    # [LOGIQUE DE SCORING (EMA, RSI, MACD, BREAKOUTS) INCHANGÉE]
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
    volatility = last['ATR'] * SL_TP_MULTIPLIER
    entry_price = last['Close']
    
    if final_score >= DECISION_THRESHOLD:
        signal = "BUY"
        stop_loss = entry_price - volatility
        take_profit = entry_price + volatility
        
    elif final_score <= -DECISION_THRESHOLD:
        signal = "SELL"
        stop_loss = entry_price + volatility
        take_profit = entry_price - volatility
        
    return signal, final_score, reasons, entry_price, stop_loss, take_profit


# ==========================================
# NOUVELLE FONCTION D'EXÉCUTION APSCHEDULER
# ==========================================

def run_analysis_job():
    """
    Exécute toute la logique d'analyse, d'alerte et de log.
    Cette fonction est appelée par le planificateur, pas par une requête Web.
    """
    try:
        print(f"\n{'='*50}\nJOB DÉMARRÉ : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # 1. Données
        df = get_market_data(SYMBOL, period="2d", interval=TIMEFRAME)
        if df is None: return

        # 2. Technique
        df = calculate_technicals(df)

        # 3. Sentiment
        sentiment_score = get_sentiment_analysis(SYMBOL)

        # 4. Décision (SL et TP inclus)
        signal, score, reasons, price, sl, tp = analyze_market_logic(df, sentiment_score)
        
        # Affichage Console (pour les logs du serveur)
        print(f"[{SYMBOL}] Prix: {price:.5f} | Score: {score:.2f} | SIGNAL: {signal}")
        
        # 5. Alerte Email (Si signal fort)
        if signal != "NEUTRAL":
            subject = f"ALERTE {signal} : {SYMBOL} (Score {score:.2f})"
            body = f"""
            🚨 ALERTE TRADING EN CONTINU
            -----------------
            Symbole : {SYMBOL}
            Signal  : {signal}
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
        
        print(f"JOB TERMINÉ. Prochaine exécution dans {EXECUTION_INTERVAL_MIN} min.")

    except Exception as e:
        print(f"ERREUR CRITIQUE dans run_analysis_job: {e}")
        # En cas d'erreur majeure, on s'assure que le job peut redémarrer
        time.sleep(60)


# ==========================================
# APPLICATION FLASK & SCHEDULER
# ==========================================

app = Flask(__name__)
scheduler = BackgroundScheduler()

# Route d'accueil pour vérifier que le service est en ligne
@app.route('/')
def status():
    """Endpoint simple pour vérifier l'état du serveur."""
    return f"""
    <h1>🤖 Bot d'Analyse {SYMBOL} en cours</h1>
    <p>Le planificateur APScheduler tourne en arrière-plan et exécute la tâche d'analyse toutes les <b>{EXECUTION_INTERVAL_MIN} minutes</b>.</p>
    <p>Vérifiez les emails pour les alertes.</p>
    """

# ==========================================
# APPLICATION FLASK & SCHEDULER (Fin de fichier)
# ==========================================

# ... (Le code Flask et Scheduler reste le même) ...

if __name__ == '__main__':
    try:
        # Démarre la tâche de planification en arrière-plan
        scheduler.add_job(func=run_analysis_job, trigger='interval', minutes=EXECUTION_INTERVAL_MIN, id='forex_analysis_job')
        scheduler.start()
        print("✅ Planificateur APScheduler démarré localement.")
        
        # Lance le serveur de DÉVELOPPEMENT (Werkzeug)
        # NE PAS UTILISER EN PRODUCTION !
        app.run(host='0.0.0.0', port=os.environ.get('PORT', 5000), threaded=True)
        
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        print("\n❌ Scheduler et Application arrêtés.")

# Gunicorn utilisera directement l'objet 'app' créé par Flask.
# Le lancement réel en production se fera via la commande Gunicorn (Étape 3).