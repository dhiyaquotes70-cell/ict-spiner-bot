# 🤖 ICT Sniper Bot — Free Hosting Guide
## Telegram Bot Create + Free Run பண்றது எப்படி

---

## STEP 1 — Telegram Bot Create பண்றது

1. Telegram-ல **@BotFather** search பண்ணு
2. `/newbot` type பண்ணு
3. Bot name கொடு (example: `ICT Sniper Bot`)
4. Username கொடு (example: `ict_sniper_signals_bot`)
5. BotFather உனக்கு ஒரு **TOKEN** தரும்
   ```
   example: 8258222864:AAENWkR9D78hZc_MB0Ja-ECvzUuKNlB-SWI
   ```
6. இந்த TOKEN-ஐ code-ல `TELEGRAM_TOKEN` -ல paste பண்ணு

### உன் Chat ID எடுக்கணும்:
1. Telegram-ல **@userinfobot** search பண்ணு
2. `/start` அனுப்பு
3. உன் **Chat ID** கிடைக்கும் (numbers மட்டும்)
4. அதை code-ல `CHAT_ID` -ல paste பண்ணு

---

## STEP 2 — Free Hosting Options (Best to Worst)

---

### 🥇 OPTION 1: Railway.app (BEST — Recommended)
**Free tier: 500 hours/month | No sleep | Easy**

1. https://railway.app போ
2. GitHub account-ல login பண்ணு
3. "New Project" → "Deploy from GitHub repo" click பண்ணு
4. உன் GitHub-ல repo create பண்ணி bot file upload பண்ணு
5. `requirements.txt` file add பண்ணு (கீழே பார்)
6. Deploy பண்ணு — automatic run ஆகும்!

**requirements.txt:**
```
yfinance==0.2.36
requests==2.31.0
schedule==1.2.1
pandas==2.1.4
numpy==1.26.2
pytz==2023.3
```

---

### 🥈 OPTION 2: Render.com (Good — Easy Setup)
**Free tier: 750 hours/month | Sleeps after 15min inactivity**

⚠️ Problem: Free tier-ல 15 mins activity இல்லன்னா sleep ஆகும்.
Fix: UptimeRobot-ல ping setup பண்ணா wake up ஆகும்.

1. https://render.com போ
2. GitHub-ல login பண்ணு
3. "New" → "Background Worker" select பண்ணு
4. GitHub repo connect பண்ணு
5. Build Command: `pip install -r requirements.txt`
6. Start Command: `python ict_sniper_bot_v3_10pairs.py`
7. Deploy!

**Sleep Fix (Free):**
1. https://uptimerobot.com போ
2. "Add New Monitor" → HTTP(s) type
3. URL: உன் Render app URL
4. Interval: 14 minutes
5. இது bot-ஐ தூங்க விடாம வச்சிருக்கும்!

---

### 🥉 OPTION 3: Koyeb.com (Decent)
**Free tier: 1 instance | No sleep**

1. https://koyeb.com போ
2. GitHub login
3. "Create App" → GitHub repo
4. Runtime: Python
5. Run command: `python ict_sniper_bot_v3_10pairs.py`
6. Deploy!

---

### 🏠 OPTION 4: உன் PC-லயே Run பண்றது (If always on)
**Free — But PC always ON வேணும்**

```bash
# Windows — CMD-ல:
pip install yfinance requests schedule pandas numpy pytz
python ict_sniper_bot_v3_10pairs.py

# PC close ஆனா bot stop ஆகும்!
# Task Scheduler-ல add பண்ணா auto-start ஆகும்
```

---

## STEP 3 — GitHub Upload (Railway/Render-க்கு வேணும்)

1. https://github.com போ → New Repository
2. Repository name: `ict-sniper-bot`
3. "Add file" → Upload files
4. இந்த 2 files upload பண்ணு:
   - `ict_sniper_bot_v3_10pairs.py`
   - `requirements.txt`
5. Commit changes!

---

## STEP 4 — Environment Variables (Security)

Token-ஐ code-ல வச்சா safe இல்ல. Railway/Render-ல இப்படி பண்ணு:

**Railway:**
Settings → Variables → Add:
```
TELEGRAM_TOKEN = உன் token
CHAT_ID = உன் chat id
```

**Code-ல இப்படி மாத்து:**
```python
import os
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
```

---

## STEP 5 — Bot Test பண்றது

Bot deploy ஆன பிறகு:
1. உன் Telegram-ல bot-ஐ open பண்ணு
2. `/start` அனுப்பு
3. Bot "ICT Sniper Bot V3 Active!" message அனுப்பும்
4. London Kill Zone (IST 1:30 PM - 4:30 PM) start ஆனா signals வரும்!

---

## Summary — எந்த Option Best?

| Option | Free Hours | Sleep? | Easy? | Best For |
|--------|-----------|--------|-------|----------|
| Railway | 500hr/mo | ❌ No | ✅ Yes | **Best choice** |
| Render | 750hr/mo | ⚠️ Yes* | ✅ Yes | Good with UptimeRobot |
| Koyeb | Unlimited | ❌ No | ✅ Yes | Alternative |
| Own PC | Unlimited | ❌ No | ✅ Yes | If PC always on |

**→ Railway.app பயன்படுத்து — எல்லாத்திலயும் easy & reliable!**

---

## Kill Zone Times (IST) — Signal எப்போ வரும்?

| Session | IST Time | Best Pairs |
|---------|----------|-----------|
| 🇬🇧 London Kill Zone | 1:30 PM - 4:30 PM | EURUSD, GBPUSD, EURGBP |
| ⚖️ London/NY Overlap | 4:30 PM - 6:30 PM | எல்லாமே |
| 🇺🇸 NY Kill Zone | 6:30 PM - 9:30 PM | USDCAD, AUDUSD, USDCHF |

---
*Made for ICT Sniper Bot V3 — 10 Pairs Edition*
