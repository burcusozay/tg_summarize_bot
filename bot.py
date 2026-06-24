import os
import google.generativeai as genai
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY")
DATABASE_URL      = os.getenv("DATABASE_URL")
TIMEZONE          = ZoneInfo("Europe/Istanbul")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")


# ── Veritabanı ────────────────────────────────────────────────

def db_baglanti():
    return psycopg2.connect(DATABASE_URL)

def db_baslat():
    with db_baglanti() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS mesajlar (
                id        SERIAL PRIMARY KEY,
                chat_id   BIGINT NOT NULL,
                kullanici TEXT,
                metin     TEXT,
                zaman     TIMESTAMPTZ NOT NULL
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_chat_zaman ON mesajlar (chat_id, zaman)")

def mesaj_ekle(chat_id, kullanici, metin, zaman):
    with db_baglanti() as con:
        con.execute(
            "INSERT INTO mesajlar (chat_id, kullanici, metin, zaman) VALUES (%s, %s, %s, %s)",
            (chat_id, kullanici, metin, zaman)
        )

def mesaj_getir(chat_id, baslangic, bitis=None):
    with db_baglanti() as con, con.cursor(cursor_factory=RealDictCursor) as cur:
        if bitis:
            cur.execute(
                "SELECT kullanici, metin, zaman FROM mesajlar WHERE chat_id=%s AND zaman>=%s AND zaman<=%s ORDER BY zaman",
                (chat_id, baslangic, bitis)
            )
        else:
            cur.execute(
                "SELECT kullanici, metin, zaman FROM mesajlar WHERE chat_id=%s AND zaman>=%s ORDER BY zaman",
                (chat_id, baslangic)
            )
        return cur.fetchall()


# ── Tarih Parser ──────────────────────────────────────────────

def parse_tarih_saat(args):
    if not args:
        return None, (
            "Kullanım:\n"
            "/ozet 14:00\n"
            "/ozet 2025-06-23\n"
            "/ozet 2025-06-23 14:00\n"
            "/ozet 2025-06-23 09:00 2025-06-23 18:00"
        )

    joined = " ".join(args)
    bugun  = datetime.now(TIMEZONE).date()

    if len(args) >= 4:
        try:
            bas = datetime.strptime(" ".join(args[:2]), "%Y-%m-%d %H:%M").replace(tzinfo=TIMEZONE)
            bit = datetime.strptime(" ".join(args[2:4]), "%Y-%m-%d %H:%M").replace(tzinfo=TIMEZONE)
            return (bas, bit), None
        except ValueError:
            pass

    for fmt, sadece_saat in [("%Y-%m-%d %H:%M", False), ("%Y-%m-%d", False), ("%H:%M", True)]:
        try:
            if sadece_saat:
                dt = datetime.strptime(joined, fmt).replace(
                    year=bugun.year, month=bugun.month, day=bugun.day, tzinfo=TIMEZONE
                )
            else:
                dt = datetime.strptime(joined, fmt).replace(tzinfo=TIMEZONE)
            return (dt, None), None
        except ValueError:
            continue

    return None, "❌ Format tanınamadı. Örnek: /ozet 2025-06-23 14:00"


# ── Handler'lar ───────────────────────────────────────────────

async def mesaj_kaydet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    mesaj_ekle(
        chat_id=update.message.chat_id,
        kullanici=update.message.from_user.first_name,
        metin=update.message.text,
        zaman=update.message.date.astimezone(TIMEZONE)
    )

async def ozet_al(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    sonuc, hata = parse_tarih_saat(context.args)
    if hata:
        await update.message.reply_text(hata)
        return

    baslangic, bitis = sonuc
    satirlar = mesaj_getir(chat_id, baslangic, bitis)

    if not satirlar:
        await update.message.reply_text("Bu zaman aralığında mesaj bulunamadı.")
        return

    await update.message.reply_text(f"⏳ {len(satirlar)} mesaj özetleniyor...")

    mesaj_metni = "\n".join(
        f"[{r['zaman'].astimezone(TIMEZONE).strftime('%H:%M')}] {r['kullanici']}: {r['metin']}"
        for r in satirlar
    )

    aralik_str = (
        f"{baslangic.strftime('%d.%m.%Y %H:%M')} – {bitis.strftime('%d.%m.%Y %H:%M')}"
        if bitis else
        f"{baslangic.strftime('%d.%m.%Y %H:%M')} sonrası"
    )

    response = model.generate_content(
        f"""Aşağıdaki Telegram grup konuşmasını Türkçe özetle.
Önemli konuları, kararları ve aksiyonları madde madde listele.
Mesaj saatleri köşeli parantez içinde verilmiştir.

Konuşma ({aralik_str}):
{mesaj_metni}"""
    )

    await update.message.reply_text(
        f"📋 *{aralik_str} özeti* ({len(satirlar)} mesaj)\n\n{response.text}",
        parse_mode="Markdown"
    )


# ── Ana Fonksiyon ─────────────────────────────────────────────

def main():
    db_baslat()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, mesaj_kaydet))
    app.add_handler(CommandHandler("ozet", ozet_al))
    print("Bot çalışıyor...")
    app.run_polling()

if __name__ == "__main__":
    main()
