# Telegram Test Bot

Bu bot 10, 20 yoki 30 talik random test beradi. Har bir savolda birinchi javob bazaga to'g'ri javob sifatida yoziladi, bot esa foydalanuvchiga ko'rsatishda A/B/C/D variantlarni aralashtiradi.

## Lokal ishga tushirish

1. Python paketlarni o'rnating:

```powershell
pip install -r requirements.txt
```

2. `.env.example` faylidan `.env` yarating:

```powershell
Copy-Item .env.example .env
```

3. `.env` ichiga tokenni yozing:

```env
BOT_TOKEN=your_token
DATABASE_URL=sqlite:///bot.db
```

4. Testlarni bazaga import qiling:

```powershell
python import_tests.py tests_2025_26.json --replace
```

5. Botni ishga tushiring:

```powershell
python bot.py
```

## Test fayl formatlari

JSON:

```json
[
  {
    "question": "Savol matni?",
    "answers": ["To'g'ri javob", "Noto'g'ri 1", "Noto'g'ri 2", "Noto'g'ri 3"],
    "info": "Ixtiyoriy izoh"
  }
]
```

CSV ustunlari:

```csv
question,answer1,answer2,answer3,answer4,info
Savol matni?,To'g'ri javob,Noto'g'ri 1,Noto'g'ri 2,Noto'g'ri 3,Ixtiyoriy izoh
```

TXT yoki DOCX:

```text
Savol matni?
To'g'ri javob
Noto'g'ri 1
Noto'g'ri 2
Noto'g'ri 3
Ixtiyoriy izoh

Keyingi savol?
To'g'ri javob
Noto'g'ri 1
Noto'g'ri 2
Noto'g'ri 3
```

## Railway

1. Railway loyihaga PostgreSQL qo'shing.
2. Variables bo'limiga `BOT_TOKEN` qo'shing.
3. Railway PostgreSQL odatda `DATABASE_URL` ni o'zi beradi.
4. Deploy qiling. `Procfile` worker jarayonni ishga tushiradi.
5. Railway shell yoki lokal terminal orqali bir marta import qiling:

```powershell
python import_tests.py tests_2025_26.json --replace
```

Muhim: tokenni GitHub yoki ommaviy joyga joylamang. Token oshkor bo'lgan bo'lsa, BotFather orqali tokenni yangilang.
