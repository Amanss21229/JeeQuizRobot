import asyncio
import logging
import random
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
)
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler, MessageHandler,
    PollAnswerHandler, ChatMemberHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from models import db

logger = logging.getLogger(__name__)

CORRECT_MSGS = [
    "Sahi jawab! Keep it up! 🎉",
    "Bilkul sahi! Bahut badhiya! ✅",
    "Perfect! Aise hi karte raho! 🔥",
    "Wah! Zabardast! 💪",
    "100% correct! Great going! 🏆",
]
WRONG_MSGS = [
    "Galat jawab! Agli baar dhyan do. 😔",
    "Nahi! Sahi answer dekho aur seekho. 📖",
    "Wrong! Practice more. 💪",
    "Iske baare mein aur padhna chahiye. 📚",
    "Galat! Mat ghabrao, try karte raho. 🙏",
]


class CloneBotInstance:
    def __init__(self, bot_token: str, clone_bot_id: int, owner_id: int,
                 bot_username: str = None, bot_name: str = None):
        self.bot_token = bot_token
        self.clone_bot_id = clone_bot_id
        self.owner_id = owner_id
        self.bot_username = bot_username or "Quiz Bot"
        self.bot_name = bot_name or "Quiz Bot"
        self.application = None
        self._broadcast_pending = {}

    def _register_handlers(self):
        app = self.application
        app.add_handler(CommandHandler("start", self.start_command))
        app.add_handler(CommandHandler("help", self.start_command))
        app.add_handler(CommandHandler("stats", self.stats_command))
        app.add_handler(CommandHandler("broadcast", self.broadcast_command))
        app.add_handler(CommandHandler("cancel", self.cancel_command))
        app.add_handler(CommandHandler("language", self.language_command))
        app.add_handler(CommandHandler("leaderboard", self.leaderboard_command))
        app.add_handler(PollAnswerHandler(self.handle_poll_answer))
        app.add_handler(ChatMemberHandler(
            self.handle_chat_member_update, ChatMemberHandler.MY_CHAT_MEMBER
        ))
        app.add_handler(CallbackQueryHandler(self.handle_callback_query))
        app.add_handler(MessageHandler(filters.ALL, self.track_groups))
        app.add_handler(
            MessageHandler(
                filters.ChatType.PRIVATE & ~filters.COMMAND,
                self.handle_private_message
            ),
            group=1
        )

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        await db.add_user(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            clone_bot_id=self.clone_bot_id
        )
        clone_info = await db.get_clone_bot(self.clone_bot_id)
        if clone_info and clone_info.get('is_paused'):
            await update.message.reply_text(
                f"⚠️ This bot is temporarily paused.\n\n"
                f"Reason: {clone_info.get('pause_reason') or 'Contact the bot owner.'}"
            )
            return
        await update.message.reply_text(
            f"👋 Welcome to **{self.bot_name}**!\n\n"
            f"🎯 Get daily JEE quizzes\n"
            f"📊 Track your scores (+4 correct, -1 wrong)\n"
            f"🏆 Compete on the leaderboard\n\n"
            f"📖 Stay active and answer daily quizzes to improve your rank!\n\n"
            f"Commands:\n"
            f"/leaderboard — See top scorers\n"
            f"/language — Change quiz language\n"
            f"/stats — Bot statistics (owner only)",
            parse_mode='Markdown'
        )

    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user.id != self.owner_id:
            await update.message.reply_text("❌ This command is only for the bot owner.")
            return
        stats = await db.get_clone_bot_stats(self.clone_bot_id)
        await update.message.reply_text(
            f"📊 **{self.bot_name} — Statistics**\n\n"
            f"👥 Total Users: **{stats.get('users', 0)}**\n"
            f"🏠 Total Groups: **{stats.get('groups', 0)}**\n"
            f"📢 Total Channels: **{stats.get('channels', 0)}**\n"
            f"📝 Total Quiz Answers: **{stats.get('total_answers', 0)}**\n",
            parse_mode='Markdown'
        )

    async def broadcast_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user.id != self.owner_id:
            await update.message.reply_text("❌ Only the bot owner can broadcast.")
            return
        if update.effective_chat.type != 'private':
            keyboard = [[InlineKeyboardButton(
                "📩 Open Private Chat",
                url=f"https://t.me/{self.bot_username}?start=broadcast"
            )]]
            await update.message.reply_text(
                "📌 Please use /broadcast in private chat with your bot.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        self._broadcast_pending[user.id] = True
        await update.message.reply_text(
            "📢 **Broadcast Mode Activated**\n\n"
            "Send me the content to broadcast to all your groups and users.\n"
            "Supports: Text, Photo, Video, Document, Audio, Sticker, GIF, Poll, etc.\n\n"
            "Send /cancel to abort.",
            parse_mode='Markdown'
        )

    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user.id in self._broadcast_pending:
            del self._broadcast_pending[user.id]
            await update.message.reply_text("❌ Broadcast cancelled.")
        else:
            await update.message.reply_text("Nothing to cancel.")

    async def language_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        chat = update.effective_chat
        keyboard = [[
            InlineKeyboardButton("🇬🇧 English", callback_data=f"clang_english_{chat.id}"),
            InlineKeyboardButton("🇮🇳 हिंदी", callback_data=f"clang_hindi_{chat.id}")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if chat.type in ['group', 'supergroup']:
            current_lang = await db.get_group_language(chat.id)
        else:
            current_lang = await db.get_user_language(user.id)
        lang_display = "English" if current_lang == 'english' else "हिंदी (Hindi)"
        await update.message.reply_text(
            f"🌐 **Language Selection**\n\n"
            f"📌 Current: **{lang_display}**\n\n"
            f"Choose quiz language:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    async def leaderboard_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        leaderboard = await db.get_clone_leaderboard(self.clone_bot_id, 10)
        if not leaderboard:
            await update.message.reply_text("📊 No scores yet. Answer quizzes to appear on the leaderboard!")
            return
        medals = ['🥇', '🥈', '🥉']
        text = f"🏆 **{self.bot_name} Leaderboard**\n\n"
        for i, entry in enumerate(leaderboard):
            medal = medals[i] if i < 3 else f"{i + 1}."
            name = entry.get('first_name') or 'User'
            score = entry.get('total_score', 0)
            text += f"{medal} {name}: **{score}** pts\n"
        await update.message.reply_text(text, parse_mode='Markdown')

    async def handle_poll_answer(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        poll_answer = update.poll_answer
        user = poll_answer.user
        poll_id = poll_answer.poll_id
        selected_options = poll_answer.option_ids

        poll_data = await db.get_poll_mapping(poll_id)
        if not poll_data:
            return

        quiz_id = poll_data['quiz_id']
        group_id = poll_data['group_id']
        correct_option = poll_data['correct_option']

        if len(selected_options) == 0:
            points = 0
        elif selected_options[0] == correct_option:
            points = 4
        else:
            points = -1

        await db.add_user(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            clone_bot_id=self.clone_bot_id
        )
        await db.add_group_member(user.id, group_id)

        try:
            await db.record_quiz_answer(
                user_id=user.id,
                group_id=group_id,
                quiz_id=quiz_id,
                selected_option=selected_options[0] if selected_options else -1,
                points=points
            )
            if points == 4:
                msg = random.choice(CORRECT_MSGS)
                emoji = "🎉"
            elif points == -1:
                msg = random.choice(WRONG_MSGS)
                emoji = "😔"
            else:
                return
            user_mention = f"[{user.first_name}](tg://user?id={user.id})"
            await context.bot.send_message(
                chat_id=group_id,
                text=f"{emoji} {user_mention} {msg}",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Clone {self.clone_bot_id}: Error recording answer: {e}")

    async def handle_chat_member_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        result = update.my_chat_member
        chat = result.chat
        new_status = result.new_chat_member.status
        if new_status in ['member', 'administrator']:
            await db.add_group(
                group_id=chat.id,
                title=chat.title or '',
                group_type=chat.type,
                clone_bot_id=self.clone_bot_id
            )
            logger.info(f"Clone {self.clone_bot_id}: Added to {chat.type} {chat.id} ({chat.title})")

    async def track_groups(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        if chat and chat.type in ['group', 'supergroup', 'channel']:
            await db.add_group(
                group_id=chat.id,
                title=chat.title or '',
                group_type=chat.type,
                clone_bot_id=self.clone_bot_id
            )

    async def handle_private_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        message = update.message
        if user.id not in self._broadcast_pending:
            return
        del self._broadcast_pending[user.id]

        groups = await db.get_clone_groups(self.clone_bot_id)
        users = await db.get_clone_users(self.clone_bot_id)

        all_targets = [g['id'] for g in groups]
        all_targets += [u['id'] for u in users if u['id'] != user.id]

        if not all_targets:
            await message.reply_text("⚠️ No users or groups to broadcast to yet.")
            return

        status_msg = await message.reply_text(f"📤 Broadcasting to {len(all_targets)} targets...")
        success = 0
        failed = 0

        for target_id in all_targets:
            try:
                await self._copy_message(context, target_id, message)
                success += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed += 1

        await status_msg.edit_text(
            f"✅ **Broadcast Complete!**\n\n"
            f"✅ Success: {success}\n"
            f"❌ Failed: {failed}",
            parse_mode='Markdown'
        )

    async def _copy_message(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, message):
        if message.text:
            return await context.bot.send_message(chat_id=chat_id, text=message.text)
        elif message.photo:
            return await context.bot.send_photo(
                chat_id=chat_id, photo=message.photo[-1].file_id, caption=message.caption)
        elif message.video:
            return await context.bot.send_video(
                chat_id=chat_id, video=message.video.file_id, caption=message.caption)
        elif message.document:
            return await context.bot.send_document(
                chat_id=chat_id, document=message.document.file_id, caption=message.caption)
        elif message.audio:
            return await context.bot.send_audio(
                chat_id=chat_id, audio=message.audio.file_id, caption=message.caption)
        elif message.sticker:
            return await context.bot.send_sticker(chat_id=chat_id, sticker=message.sticker.file_id)
        elif message.animation:
            return await context.bot.send_animation(
                chat_id=chat_id, animation=message.animation.file_id, caption=message.caption)
        elif message.voice:
            return await context.bot.send_voice(chat_id=chat_id, voice=message.voice.file_id)
        elif message.poll:
            return await context.bot.send_poll(
                chat_id=chat_id,
                question=message.poll.question,
                options=[opt.text for opt in message.poll.options],
                is_anonymous=message.poll.is_anonymous
            )
        else:
            return await context.bot.copy_message(
                chat_id=chat_id,
                from_chat_id=message.chat_id,
                message_id=message.message_id
            )

    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if query.data.startswith("clang_"):
            parts = query.data.split("_")
            if len(parts) == 3:
                language = parts[1]
                chat_id = int(parts[2])
                chat = await context.bot.get_chat(chat_id)
                if chat.type == 'private':
                    await db.set_user_language(chat_id, language)
                else:
                    await db.set_group_language(chat_id, language)
                lang_display = "English 🇬🇧" if language == 'english' else "हिंदी 🇮🇳"
                await query.edit_message_text(
                    f"✅ Language updated to **{lang_display}**",
                    parse_mode='Markdown'
                )

    async def run(self):
        self.application = ApplicationBuilder().token(self.bot_token).build()
        self._register_handlers()
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()
        logger.info(f"Clone bot {self.clone_bot_id} (@{self.bot_username}) started!")
        await asyncio.Event().wait()

    async def stop(self):
        if self.application:
            try:
                await self.application.updater.stop()
                await self.application.stop()
                await self.application.shutdown()
            except Exception as e:
                logger.error(f"Error stopping clone {self.clone_bot_id}: {e}")
        logger.info(f"Clone bot {self.clone_bot_id} stopped.")


class CloneBotManager:
    def __init__(self):
        self.instances: dict = {}
        self.tasks: dict = {}

    async def start_clone(self, bot_token: str, clone_bot_id: int, owner_id: int,
                          bot_username: str = None, bot_name: str = None):
        if clone_bot_id in self.instances:
            logger.warning(f"Clone {clone_bot_id} already running, skipping.")
            return
        instance = CloneBotInstance(bot_token, clone_bot_id, owner_id, bot_username, bot_name)
        self.instances[clone_bot_id] = instance
        task = asyncio.create_task(instance.run())
        self.tasks[clone_bot_id] = task
        logger.info(f"Clone bot {clone_bot_id} (@{bot_username}) launched.")

    async def stop_clone(self, clone_bot_id: int):
        if clone_bot_id in self.instances:
            instance = self.instances.pop(clone_bot_id)
            await instance.stop()
        if clone_bot_id in self.tasks:
            task = self.tasks.pop(clone_bot_id)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        logger.info(f"Clone bot {clone_bot_id} fully stopped.")

    async def start_all_clones(self, exclude_bot_id: int = None):
        clones = await db.get_all_active_clone_bots()

        for clone in clones:
            try:
                clone_bot_id = int(clone["bot_id"])
             
                # Critical safety guard:
                # never start the main bot token as a clone.
                if (
                    exclude_bot_id is not None
                    and clone_bot_id == int(exclude_bot_id)
                ):
                    logger.error(
                        "Skipping invalid clone record because it matches "
                        "the main bot ID: %s",
                        clone_bot_id
                    )
                    continue
                    
                await self.start_clone(
                    bot_token=clone["bot_token"],
                    clone_bot_id=clone_bot_id,
                    owner_id=clone["owner_id"],
                    bot_username=clone.get("bot_username"),
                    bot_name=clone.get("bot_name")
                )
            
            except Exception as e:
                logger.error(
                    "Failed to start clone %s: %s",
                    clone.get("bot_id"),
                    e
                )    

    def get_all_instances(self) -> dict:
        return self.instances

    def get_instance(self, clone_bot_id: int):
        return self.instances.get(clone_bot_id)


clone_manager = CloneBotManager()
