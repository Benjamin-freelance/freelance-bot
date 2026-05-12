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
        user_state[user_id] = {"step": "AWAIT_ACTION", "brief": "", "content": "", "lang": "EN"}
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
        model="claude-sonnet-4-5",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_state[user_id] = {"step": "AWAIT_ACTION", "brief": "", "content": "", "lang": "EN"}
    
    keyboard = [
        [InlineKeyboardButton("✍️ Générer un contenu", callback_data="action_content")],
        [InlineKeyboardButton("💬 Répondre à un client", callback_data="action_reply")]
    ]
    await update.message.reply_text(
        "👋 Salut Benjamin! Que veux-tu faire ?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = get_state(user_id)
    text = update.message.text.strip()

    # GÉNÉRATION DE CONTENU
    if state["step"] == "AWAIT_BRIEF":
        state["brief"] = text
        state["step"] = "GENERATING"
        await update.message.reply_text("⏳ Génération en cours, patiente 30-60 secondes...")

        content_type, word_count = detect_type(text)
        lang_map = {"FR": "français", "EN": "English", "DE": "Deutsch"}
        lang = lang_map.get(state["lang"], "English")
        
        prompt = (
            f"Tu es un redacteur web professionnel freelance multilingue base en Suisse.\n"
            f"Genere un {content_type} de {word_count} base sur ce brief : \"{text}\"\n"
            f"Redige UNIQUEMENT en {lang}.\n"
            f"Structure bien le contenu. Sois accrocheur, clair et professionnel.\n"
            f"Reponds UNIQUEMENT avec le contenu redige, sans introduction ni commentaire."
        )

        content = call_claude(prompt)
        state["content"] = content
        state["step"] = "AWAIT_VALIDATION"

        keyboard = [
            [InlineKeyboardButton("✅ Valider et livrer", callback_data="validate")],
            [InlineKeyboardButton("✏️ Modifier", callback_data="modify")],
            [InlineKeyboardButton("🔄 Tout refaire", callback_data="regenerate")]
        ]
        await update.message.reply_text(
            f"Contenu genere:\n\n{content}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # RÉPONSE CLIENT
    elif state["step"] == "AWAIT_CLIENT_MSG":
        state["brief"] = text
        state["step"] = "GENERATING_REPLY"
        await update.message.reply_text("⏳ Génération de la réponse...")

        lang_map = {"FR": "français", "EN": "English", "DE": "Deutsch"}
        lang = lang_map.get(state["lang"], "English")

        prompt = (
            f"Tu es Benjamin, un redacteur web freelance professionnel et multilingue base en Suisse.\n"
            f"Un client t'a envoye ce message sur Fiverr ou Malt : \"{text}\"\n"
            f"Redige une reponse professionnelle, chaleureuse et concise en {lang}.\n"
            f"Mets en valeur ta disponibilite, tes competences en redaction FR/EN/DE et ta rapidite de livraison.\n"
            f"Reponds UNIQUEMENT avec le texte de la reponse, sans introduction ni commentaire."
        )

        reply = call_claude(prompt)
        state["content"] = reply
        state["step"] = "AWAIT_REPLY_VALIDATION"

        keyboard = [
            [InlineKeyboardButton("✅ Copier et envoyer", callback_data="validate_reply")],
            [InlineKeyboardButton("✏️ Modifier", callback_data="modify_reply")],
            [InlineKeyboardButton("🔄 Refaire", callback_data="regenerate_reply")]
        ]
        await update.message.reply_text(
            f"💬 Réponse générée:\n\n{reply}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif state["step"] == "AWAIT_MODIFICATION":
        state["step"] = "GENERATING"
        await update.message.reply_text("⏳ Modification en cours...")

        prompt = (
            f"Voici un contenu redige:\n\n{state['content']}\n\n"
            f"Instruction: {text}\n\n"
            f"Reponds UNIQUEMENT avec le contenu modifie."
        )

        content = call_claude(prompt)
        state["content"] = content
        state["step"] = "AWAIT_VALIDATION"

        keyboard = [
            [InlineKeyboardButton("✅ Valider et livrer", callback_data="validate")],
            [InlineKeyboardButton("✏️ Modifier encore", callback_data="modify")],
            [InlineKeyboardButton("🔄 Refaire", callback_data="regenerate")]
        ]
        await update.message.reply_text(
            f"Contenu modifie:\n\n{content}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif state["step"] == "AWAIT_REPLY_MODIFICATION":
        state["step"] = "GENERATING_REPLY"
        await update.message.reply_text("⏳ Modification en cours...")

        prompt = (
            f"Voici une reponse client:\n\n{state['content']}\n\n"
            f"Instruction: {text}\n\n"
            f"Reponds UNIQUEMENT avec la reponse modifiee."
        )

        reply = call_claude(prompt)
        state["content"] = reply
        state["step"] = "AWAIT_REPLY_VALIDATION"

        keyboard = [
            [InlineKeyboardButton("✅ Copier et envoyer", callback_data="validate_reply")],
            [InlineKeyboardButton("✏️ Modifier encore", callback_data="modify_reply")]
        ]
        await update.message.reply_text(
            f"💬 Réponse modifiée:\n\n{reply}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    state = get_state(user_id)
    await query.answer()

    # CHOIX DE L'ACTION
    if query.data == "action_content":
        state["step"] = "AWAIT_LANG_CONTENT"
        await query.edit_message_reply_markup(None)
        keyboard = [
            [InlineKeyboardButton("🇫🇷 Français", callback_data="lang_FR_content")],
            [InlineKeyboardButton("🇬🇧 English", callback_data="lang_EN_content")],
            [InlineKeyboardButton("🇩🇪 Deutsch", callback_data="lang_DE_content")]
        ]
        await context.bot.send_message(
            chat_id=user_id,
            text="Dans quelle langue veux-tu générer le contenu ?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "action_reply":
        state["step"] = "AWAIT_LANG_REPLY"
        await query.edit_message_reply_markup(None)
        keyboard = [
            [InlineKeyboardButton("🇫🇷 Français", callback_data="lang_FR_reply")],
            [InlineKeyboardButton("🇬🇧 English", callback_data="lang_EN_reply")],
            [InlineKeyboardButton("🇩🇪 Deutsch", callback_data="lang_DE_reply")]
        ]
        await context.bot.send_message(
            chat_id=user_id,
            text="Dans quelle langue veux-tu répondre au client ?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # LANGUE CONTENU
    elif query.data in ["lang_FR_content", "lang_EN_content", "lang_DE_content"]:
        state["lang"] = query.data.split("_")[1]
        state["step"] = "AWAIT_BRIEF"
        await query.edit_message_reply_markup(None)
        await context.bot.send_message(
            chat_id=user_id,
            text="Envoie-moi le brief de ta mission :"
        )

    # LANGUE RÉPONSE
    elif query.data in ["lang_FR_reply", "lang_EN_reply", "lang_DE_reply"]:
        state["lang"] = query.data.split("_")[1]
        state["step"] = "AWAIT_CLIENT_MSG"
        await query.edit_message_reply_markup(None)
        await context.bot.send_message(
            chat_id=user_id,
            text="Colle le message du client ici :"
        )

    # VALIDATION CONTENU
    elif query.data == "validate":
        state["step"] = "AWAIT_ACTION"
        await query.edit_message_reply_markup(None)
        keyboard = [
            [InlineKeyboardButton("✍️ Nouvelle mission", callback_data="action_content")],
            [InlineKeyboardButton("💬 Répondre à un client", callback_data="action_reply")]
        ]
        await context.bot.send_message(
            chat_id=user_id,
            text="✅ Mission accomplie! Copie le contenu et livre-le sur Malt ou Fiverr.\n\nQue veux-tu faire ensuite ?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "modify":
        state["step"] = "AWAIT_MODIFICATION"
        await query.edit_message_reply_markup(None)
        await context.bot.send_message(chat_id=user_id, text="Dis-moi ce que tu veux modifier :")

    elif query.data == "regenerate":
        state["step"] = "GENERATING"
        await query.edit_message_reply_markup(None)
        await context.bot.send_message(chat_id=user_id, text="🔄 Nouvelle version en cours...")

        lang_map = {"FR": "français", "EN": "English", "DE": "Deutsch"}
        lang = lang_map.get(state["lang"], "English")
        content_type, word_count = detect_type(state["brief"])
        prompt = (
            f"Genere une version COMPLETEMENT DIFFERENTE de {word_count} en {lang} sur ce brief: \"{state['brief']}\". "
            f"Reponds UNIQUEMENT avec le contenu."
        )
        content = call_claude(prompt)
        state["content"] = content
        state["step"] = "AWAIT_VALIDATION"

        keyboard = [
            [InlineKeyboardButton("✅ Valider et livrer", callback_data="validate")],
            [InlineKeyboardButton("✏️ Modifier", callback_data="modify")],
            [InlineKeyboardButton("🔄 Refaire encore", callback_data="regenerate")]
        ]
        await context.bot.send_message(
            chat_id=user_id,
            text=f"Nouvelle version:\n\n{content}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # VALIDATION RÉPONSE CLIENT
    elif query.data == "validate_reply":
        state["step"] = "AWAIT_ACTION"
        await query.edit_message_reply_markup(None)
        keyboard = [
            [InlineKeyboardButton("✍️ Générer un contenu", callback_data="action_content")],
            [InlineKeyboardButton("💬 Répondre à un client", callback_data="action_reply")]
        ]
        await context.bot.send_message(
            chat_id=user_id,
            text="✅ Copie la réponse et envoie-la au client!\n\nQue veux-tu faire ensuite ?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "modify_reply":
        state["step"] = "AWAIT_REPLY_MODIFICATION"
        await query.edit_message_reply_markup(None)
        await context.bot.send_message(chat_id=user_id, text="Dis-moi ce que tu veux modifier :")

    elif query.data == "regenerate_reply":
        state["step"] = "GENERATING_REPLY"
        await query.edit_message_reply_markup(None)
        await context.bot.send_message(chat_id=user_id, text="🔄 Nouvelle réponse en cours...")

        lang_map = {"FR": "français", "EN": "English", "DE": "Deutsch"}
        lang = lang_map.get(state["lang"], "English")
        prompt = (
            f"Tu es Benjamin, un redacteur web freelance professionnel.\n"
            f"Genere une reponse COMPLETEMENT DIFFERENTE en {lang} pour ce message client: \"{state['brief']}\"\n"
            f"Reponds UNIQUEMENT avec la reponse."
        )
        reply = call_claude(prompt)
        state["content"] = reply
        state["step"] = "AWAIT_REPLY_VALIDATION"

        keyboard = [
            [InlineKeyboardButton("✅ Copier et envoyer", callback_data="validate_reply")],
            [InlineKeyboardButton("✏️ Modifier", callback_data="modify_reply")],
            [InlineKeyboardButton("🔄 Refaire encore", callback_data="regenerate_reply")]
        ]
        await context.bot.send_message(
            chat_id=user_id,
            text=f"Nouvelle réponse:\n\n{reply}",
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
