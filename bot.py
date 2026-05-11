import os
import logging
import anthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY")

user_state = {}

def get_state(user_id):
    if user_id not in user_state:
        user_state[user_id] = {"step": "AWAIT_BRIEF", "brief": "", "content": ""}
    return user_state[user_id]

def detect_type(brief):
    b = brief.lower()
    if any(w in b for w in ["linkedin", "post", "reseau", "social"]):
        return "post LinkedIn professionnel", "200 mots"
    elif "instagram" in b:
        return "post Instagram", "150 mots"
    elif any(w in b for w in ["fiche", "produit", "ecommerce", "e-commerce"]):
        return "fiche produit e-commerce", "120 mots"
    else:
        return "article de blog", "800 mots"

def call_claude(prompt):
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_state[user_id] = {"step": "AWAIT_BRIEF", "brief": "", "content": ""}
    await update.message.reply_text(
        "👋 Salut Benjamin! Ton agent IA freelance est pret.\n\n"
        "Envoie-moi le brief de ta mission et je genere le contenu complet.\n\n"
        "Exemple: Article blog 800 mots sur les tendances IA 2026, ton professionnel, cible PME"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = get_state(user_id)
    text = update.message.text.strip()

    if state["step"] == "AWAIT_BRIEF":
        state["brief"] = text
        state["step"] = "GENERATING"
        await update.message.reply_text("Génération en cours, patiente 30-60 secondes...")

        content_type, word_count = detect_type(text)
        prompt = (
            f"Tu es un redacteur web professionnel freelance multilingue base en Suisse.\n"
            f"Genere un {content_type} de {word_count} base sur ce brief : \"{text}\"\n"
            f"Structure bien le contenu. Sois accrocheur, clair et professionnel.\n"
            f"Reponds UNIQUEMENT avec le contenu redige, sans introduction ni commentaire."
        )

        content = call_claude(prompt)
        state["content"] = content
        state["step"] = "AWAIT_VALIDATION"

        keyboard = [
            [InlineKeyboardButton("Valider et livrer", callback_data="validate")],
            [InlineKeyboardButton("Modifier", callback_data="modify")],
            [InlineKeyboardButton("Tout refaire", callback_data="regenerate")]
        ]

        await update.message.reply_text(
            f"Contenu genere:\n\n{content}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif state["step"] == "AWAIT_MODIFICATION":
        state["step"] = "GENERATING"
        await update.message.reply_text("Modification en cours...")

        prompt = (
            f"Voici un contenu redige:\n\n{state['content']}\n\n"
            f"Instruction: {text}\n\n"
            f"Reponds UNIQUEMENT avec le contenu modifie."
        )

        content = call_claude(prompt)
        state["content"] = content
        state["step"] = "AWAIT_VALIDATION"

        keyboard = [
            [InlineKeyboardButton("Valider et livrer", callback_data="validate")],
            [InlineKeyboardButton("Modifier encore", callback_data="modify")],
            [InlineKeyboardButton("Refaire", callback_data="regenerate")]
        ]

        await update.message.reply_text(
            f"Contenu modifie:\n\n{content}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    state = get_state(user_id)
    await query.answer()

    if query.data == "validate":
        state["step"] = "AWAIT_BRIEF"
        await query.edit_message_reply_markup(None)
        await context.bot.send_message(
            chat_id=user_id,
            text="Mission accomplie! Copie le contenu et livre-le sur Malt ou Fiverr.\n\nEnvoie un nouveau brief pour la prochaine mission!"
        )

    elif query.data == "modify":
        state["step"] = "AWAIT_MODIFICATION"
        await query.edit_message_reply_markup(None)
        await context.bot.send_message(
            chat_id=user_id,
            text="Dis-moi ce que tu veux modifier:"
        )

    elif query.data == "regenerate":
        state["step"] = "GENERATING"
        await query.edit_message_reply_markup(None)
        await context.bot.send_message(chat_id=user_id, text="Nouvelle version en cours...")

        content_type, word_count = detect_type(state["brief"])
        prompt = (
            f"Genere une version COMPLETEMENT DIFFERENTE de {word_count} sur ce brief: \"{state['brief']}\". "
            f"Reponds UNIQUEMENT avec le contenu."
        )

        content = call_claude(prompt)
        state["content"] = content
        state["step"] = "AWAIT_VALIDATION"

        keyboard = [
            [InlineKeyboardButton("Valider et livrer", callback_data="validate")],
            [InlineKeyboardButton("Modifier", callback_data="modify")],
            [InlineKeyboardButton("Refaire encore", callback_data="regenerate")]
        ]

        await context.bot.send_message(
            chat_id=user_id,
            text=f"Nouvelle version:\n\n{content}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))
    print("Bot demarre!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
