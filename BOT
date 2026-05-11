import os
import anthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY")

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

user_state = {}

def get_state(user_id):
    if user_id not in user_state:
        user_state[user_id] = {"step": "AWAIT_BRIEF", "brief": "", "content": ""}
    return user_state[user_id]

def detect_type(brief):
    b = brief.lower()
    if any(w in b for w in ["linkedin", "post", "réseau", "social"]):
        return "post LinkedIn professionnel", "200 mots"
    elif any(w in b for w in ["instagram"]):
        return "post Instagram", "150 mots"
    elif any(w in b for w in ["fiche", "produit", "e-commerce"]):
        return "fiche produit e-commerce", "120 mots"
    else:
        return "article de blog", "800 mots"

def call_claude(prompt):
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
        "👋 Salut Benjamin ! Ton agent IA freelance est prêt.\n\n"
        "Envoie-moi le brief de ta mission et je génère le contenu complet.\n\n"
        "📝 Exemple :\n"
        "_Article blog 800 mots sur les tendances IA en 2026, ton professionnel, cible PME_",
        parse_mode="Markdown"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = get_state(user_id)
    text = update.message.text.strip()

    if state["step"] == "AWAIT_BRIEF":
        state["brief"] = text
        state["step"] = "GENERATING"
        await update.message.reply_text("⏳ Génération en cours...")

        content_type, word_count = detect_type(text)
        prompt = (
            f"Tu es un rédacteur web professionnel freelance multilingue basé en Suisse.\n"
            f"Génère un {content_type} de {word_count} basé sur ce brief client : \"{text}\"\n"
            f"Structure bien le contenu. Sois accrocheur, clair et professionnel.\n"
            f"Réponds UNIQUEMENT avec le contenu rédigé, sans introduction ni commentaire."
        )

        content = call_claude(prompt)
        state["content"] = content
        state["step"] = "AWAIT_VALIDATION"

        keyboard = [
            [InlineKeyboardButton("✅ Valider et livrer", callback_data="validate")],
            [InlineKeyboardButton("✏️ Modifier", callback_data="modify")],
            [InlineKeyboardButton("🔄 Tout refaire", callback_data="regenerate")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"✨ *Contenu généré :*\n\n{content}",
            parse_mode="Markdown",
            reply_markup=reply_markup
        )

    elif state["step"] == "AWAIT_MODIFICATION":
        state["step"] = "GENERATING"
        await update.message.reply_text("⏳ Modification en cours...")

        prompt = (
            f"Voici un contenu rédigé :\n\n{state['content']}\n\n"
            f"Instruction de modification : {text}\n\n"
            f"Réponds UNIQUEMENT avec le contenu modifié."
        )

        content = call_claude(prompt)
        state["content"] = content
        state["step"] = "AWAIT_VALIDATION"

        keyboard = [
            [InlineKeyboardButton("✅ Valider et livrer", callback_data="validate")],
            [InlineKeyboardButton("✏️ Modifier encore", callback_data="modify")],
            [InlineKeyboardButton("🔄 Refaire", callback_data="regenerate")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"✨ *Contenu modifié :*\n\n{content}",
            parse_mode="Markdown",
            reply_markup=reply_markup
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
            text=(
                "🎉 *Mission accomplie !*\n\n"
                "Copie le contenu ci-dessus et livre-le à ton client sur Malt ou Fiverr.\n\n"
                "Envoie un nouveau brief quand tu es prêt pour la prochaine mission !"
            ),
            parse_mode="Markdown"
        )

    elif query.data == "modify":
        state["step"] = "AWAIT_MODIFICATION"
        await query.edit_message_reply_markup(None)
        await context.bot.send_message(
            chat_id=user_id,
            text="✏️ Dis-moi ce que tu veux modifier :\n\n_Ex: Rends le ton plus décontracté, ajoute une conclusion plus percutante..._",
            parse_mode="Markdown"
        )

    elif query.data == "regenerate":
        state["step"] = "GENERATING"
        await query.edit_message_reply_markup(None)
        await context.bot.send_message(chat_id=user_id, text="🔄 Nouvelle version en cours...")

        content_type, word_count = detect_type(state["brief"])
        prompt = (
            f"Tu es un rédacteur web professionnel. "
            f"Génère une version COMPLÈTEMENT DIFFÉRENTE de {word_count} basée sur ce brief : \"{state['brief']}\". "
            f"Réponds UNIQUEMENT avec le contenu."
        )

        content = call_claude(prompt)
        state["content"] = content
        state["step"] = "AWAIT_VALIDATION"

        keyboard = [
            [InlineKeyboardButton("✅ Valider et livrer", callback_data="validate")],
            [InlineKeyboardButton("✏️ Modifier", callback_data="modify")],
            [InlineKeyboardButton("🔄 Refaire encore", callback_data="regenerate")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await context.bot.send_message(
            chat_id=user_id,
            text=f"✨ *Nouvelle version :*\n\n{content}",
            parse_mode="Markdown",
            reply_markup=reply_markup
        )

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))
    print("Bot démarré !")
    app.run_polling()

if __name__ == "__main__":
    main()
