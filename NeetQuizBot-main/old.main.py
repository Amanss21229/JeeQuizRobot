import asyncio
import logging
import os
import random
from datetime import datetime, timezone
from typing import Dict, List

import pytz
from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    BotCommand,
    Poll,
    PollAnswer,
    ChatMember,
    Message
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PollAnswerHandler,
    ChatMemberHandler,
    ContextTypes,
    filters
)

from models import db

# Bot configuration
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required. Please set it with your bot token from @BotFather")
ADMIN_GROUP_ID = -1002848830142
TIMEZONE = pytz.timezone('Asia/Kolkata')

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Security: Hide bot token from logs by reducing httpx and telegram logging level
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('telegram').setLevel(logging.WARNING)

# Congratulatory messages for correct answers
CORRECT_MESSAGES = [
    "🔥 You rocked it! +4 points!",
    "🤩 waah re babua, bilkul shi jawaab! +4 points!",
    "🎉 Absolutely right! +4 points!",
    "😍 ohho ek dam shi jawaab! +4 points!",
    "✨ Brilliant answer! +4 points!",
    "😇 waah bete tune to moj kardi, bikila shi jawaab! +4 points!",
    "🚀 Outstanding! +4 points!",
    "😚 lagta hai delhi aiims jaake hi maanoge, bilkul shi jawaab! +4 points!",
    "🏆 Perfect! +4 points!",
    "🔥 Tune to Aag lga di beta, bilkul shi answer! +4 points!",
    "⭐ Excellent work! +4 points!",
    "🎯 7 crore, bilkul shi jawaab! +4 points!",
    "💯 Spot on! +4 points!",
    "🌟 Amazing! +4 points!",
    "🔥 Fantastic! +4 points!"
]

# Sad/funny messages for wrong answers
WRONG_MESSAGES = [
    "😢 Beda garak! -1 point",
    "🤦‍♂️ Padho beta padho! -1 point",
    "😅 Oops! Better luck next time! -1 point",
    "💔 So close, yet so far! -1 point",
    "😔 Not quite right! -1 point",
    "🙈 Try again! -1 point",
    "😞 Almost there! -1 point",
    "☻️ sirf reproduction padhne se jee nhi nikala beta🤡! -1 point", 
    "🤷‍♂️ Galat jawab! -1 point",
    "😵 Wrong choice! -1 point",
    "🤕 Thoda aur mehnat! -1 point",
    "🥲 kyu nhi ho rhi padhai! -1 point",
    "🤒 Dekha Laparwaahi ka naatiza! -1 point",
    "😪 chrome dekhna kam karo beta! -1 point",
    "😑 galat jawaab, extra class ke naam pe kha jaate the beta🤭! -1 point",
    "😌 wrong answer, BTW Tum to one shot waale ho na! -1 point",
    "🙃 Galat jawaab, Or pinki ke chakkar me😆! -1 point",
    "👀 jb laiki se dhyaan hatega tabhi to anser shi hoga☻️! -1 point",
    "😒 Galat jawaab, or karo babu sona🤧! -1 point",
    "😶 wrong Answer, btw tum to whi ho na jo tg pe padhne aaye the or group owner se pyaar Kar baithe🤣! -1 point",
    "😮‍💨 Wrong answer, waise wo tum hi ho na jo Har group me 'i need study partner' message karta hai😂! -1 point" 
]

class JEEQuizBot:
    def __init__(self):
        self.application = None
        self.quiz_data = {}  # Store active quizzes
        self.poll_mapping = {}  # Store poll_id -> {quiz_id, group_id, message_id}
    
    async def initialize(self):
        """Initialize the bot and database"""
        await db.init_pool()
        
        # Create bot application
        self.application = Application.builder().token(BOT_TOKEN).build()
        
        # Add default admin (you can add your user ID here)
        try:
            await db.add_admin(
                user_id=6195713937,  # Add your actual user ID here
                username="thegodoftgbot",
                first_name="Admin"
            )
        except:
            pass  # Admin might already exist
        
        # Register handlers
        self._register_handlers()
        
        # Set bot commands
        await self._set_bot_commands()
        
        # Schedule daily leaderboards at 10:00 PM IST
        self.application.job_queue.run_daily(
            callback=self.send_daily_leaderboards,
            time=datetime.strptime("22:00", "%H:%M").time(),
            name="daily_leaderboards"
        )
    
    def _register_handlers(self):
        """Register all bot handlers"""
        # Command handlers
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("refresh", self.refresh_command))
        self.application.add_handler(CommandHandler("donate", self.donate_command))
        self.application.add_handler(CommandHandler("developer", self.developer_command))
        self.application.add_handler(CommandHandler("leaderboard", self.leaderboard_command))
        
        # Admin commands
        self.application.add_handler(CommandHandler("broadcast", self.broadcast_command))
        self.application.add_handler(CommandHandler("stats", self.stats_command))
        self.application.add_handler(CommandHandler("promote", self.promote_command))
        self.application.add_handler(CommandHandler("remove", self.remove_command))
        self.application.add_handler(CommandHandler("adminlist", self.adminlist_command))
        
        # Poll and quiz handlers
        self.application.add_handler(MessageHandler(filters.POLL, self.handle_quiz))
        self.application.add_handler(MessageHandler(filters.TEXT & filters.REPLY, self.handle_reply_to_poll))
        self.application.add_handler(PollAnswerHandler(self.handle_poll_answer))
        
        # Callback query handler
        self.application.add_handler(CallbackQueryHandler(self.handle_callback_query))
        
        # Chat member handler for new groups
        self.application.add_handler(ChatMemberHandler(
            self.handle_chat_member_update, 
            ChatMemberHandler.MY_CHAT_MEMBER
        ))
    
    async def _set_bot_commands(self):
        """Set bot commands menu"""
        commands = [
            BotCommand("start", "Start the bot"),
            BotCommand("refresh", "Refresh the bot"),
            BotCommand("donate", "Support the bot"),
            BotCommand("developer", "Meet the developer"),
            BotCommand("leaderboard", "Show group leaderboard"),
        ]
        
        admin_commands = [
            BotCommand("broadcast", "Broadcast message"),
            BotCommand("stats", "Show bot statistics"),
            BotCommand("promote", "Promote user as admin"),
            BotCommand("remove", "Remove admin"),
            BotCommand("adminlist", "Show admin list"),
        ]
        
        await self.application.bot.set_my_commands(commands)
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user
        chat = update.effective_chat
        
        # Add user to database
        await db.add_user(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name
        )
        
        # If in group, add user as group member
        if chat.type in ['group', 'supergroup']:
            await db.add_group(chat.id, chat.title, chat.type)
            await db.add_group_member(user.id, chat.id)
        
        # Create inline keyboard
        keyboard = [
            [InlineKeyboardButton("➕ Add Me in Your Group", url=f"https://t.me/{context.bot.username}?startgroup=true")],
            [InlineKeyboardButton("👤 Meet the Owner", url="https://t.me/thegodoftgbot")],
            [InlineKeyboardButton("📢 Join Our Community", url="https://t.me/DrQuizBotUpdates")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_text = f"""
🎓 **Welcome to JEE Quiz Bot!** 

Hello {user.first_name}! 👋

I'm your dedicated JEE quiz companion, designed to help you ace your engineering entrance exams! 🏥📚

**What I can do:**
✅ Forward quizzes from admin group to all connected groups
🏆 Track your performance with points system (+4 correct, -1 wrong)
📊 Daily leaderboards at 10:00 PM IST
💫 Automatic quiz management

**Getting Started:**
1️⃣ Add me to your study groups
2️⃣ Start solving quizzes when they appear
3️⃣ Check daily leaderboards for your progress

Let's ace JEE together! 🚀
        """
        
        await update.message.reply_text(
            welcome_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def handle_chat_member_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle bot being added to groups"""
        chat = update.effective_chat
        my_member = update.my_chat_member
        
        if my_member.new_chat_member.status in ['member', 'administrator']:
            # Bot was added to group
            await db.add_group(chat.id, chat.title, chat.type)
            logger.info(f"Bot added to group: {chat.title} ({chat.id})")
    
    async def handle_quiz(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle quiz messages from admin group"""
        message = update.message
        poll = message.poll
        chat = update.effective_chat
        
        # Only process polls from admin group
        if chat.id != ADMIN_GROUP_ID:
            return
        
        # Accept both quiz and regular poll types, but prefer quiz
        if not poll:
            return
        
        # For regular polls, we'll treat the first option as correct by default
        poll_type = poll.type
        
        try:
            # Validate quiz data
            if not poll.question or not poll.options:
                logger.error("Invalid quiz: missing question or options")
                return
            
            # Handle correct option ID with detailed debugging
            correct_option_id = poll.correct_option_id
            
            # Debug logging to understand what's happening
            logger.info(f"Poll debug info:")
            logger.info(f"  - Poll type: {poll.type}")
            logger.info(f"  - Question: {poll.question[:50]}...")
            logger.info(f"  - Options count: {len(poll.options)}")
            logger.info(f"  - correct_option_id from poll: {correct_option_id}")
            logger.info(f"  - Poll allows_multiple_answers: {poll.allows_multiple_answers}")
            
            # Handle missing correct_option_id (Telegram API limitation) 
            # Do NOT auto-forward quiz without admin setting correct answer
            if correct_option_id is None:
                logger.info(f"⚠️ Quiz received without correct_option_id. Waiting for admin to set correct answer.")
                
                # Store quiz in database with placeholder correct_option (-1 means unset)
                options = [option.text for option in poll.options]
                quiz_id = await db.add_quiz(
                    message_id=message.message_id,
                    from_group_id=chat.id,
                    quiz_text=poll.question,
                    correct_option=-1,  # -1 indicates no correct answer set yet
                    options=options
                )
                
                # Store quiz data for tracking (without correct_option initially)
                self.quiz_data[quiz_id] = {
                    'correct_option': -1,  # Will be updated when admin replies
                    'question': poll.question,
                    'options': options,
                    'message_id': message.message_id,  # Store for reply matching
                    'poll_object': poll  # Store full poll for later forwarding
                }
                
                # Send instruction message to admin
                instruction_text = f"""
📝 **Quiz Received!**

🎯 Question: {poll.question[:100]}{'...' if len(poll.question) > 100 else ''}

⏳ **Please reply to the quiz with correct option:**
• Type: `a`, `b`, `c`, `d` or `1`, `2`, `3`, `4`
• Example: Just reply with `c`

⏰ **Quiz will be forwarded to groups 30 seconds after you set the correct answer.**
                """
                
                await context.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=instruction_text,
                    parse_mode='Markdown'
                )
                
                logger.info(f"📋 Quiz {quiz_id} waiting for admin to set correct answer")
                return  # Don't forward yet
            
            if correct_option_id < 0 or correct_option_id >= len(poll.options):
                logger.error(f"Invalid correct_option_id: {correct_option_id} for {len(poll.options)} options")
                return
            
            # This part only runs for quizzes that already have correct_option_id set
            logger.info(f"✅ Quiz has correct_option_id: {correct_option_id}")
            
            # Store quiz in database
            options = [option.text for option in poll.options]
            quiz_id = await db.add_quiz(
                message_id=message.message_id,
                from_group_id=chat.id,
                quiz_text=poll.question,
                correct_option=correct_option_id,
                options=options
            )
            
            # Store quiz data for tracking
            self.quiz_data[quiz_id] = {
                'correct_option': correct_option_id,
                'question': poll.question,
                'options': options,
                'message_id': message.message_id,
                'poll_object': poll
            }
            
            # Schedule delayed forwarding (30 seconds)
            await self._schedule_quiz_forwarding(quiz_id, context)
            
            logger.info(f"⏰ Quiz {quiz_id} scheduled for forwarding in 30 seconds")
            
        except Exception as e:
            logger.error(f"Error handling quiz: {e}")
    
    async def _schedule_quiz_forwarding(self, quiz_id: int, context: ContextTypes.DEFAULT_TYPE):
        """Schedule quiz forwarding after 30 second delay"""
        self.application.job_queue.run_once(
            callback=self._forward_quiz_to_groups,
            when=30,  # 30 seconds delay
            name=f"forward_quiz_{quiz_id}",
            data={'quiz_id': quiz_id}
        )
        logger.info(f"⏰ Quiz {quiz_id} scheduled for forwarding in 30 seconds")
    
    async def _forward_quiz_to_groups(self, context: ContextTypes.DEFAULT_TYPE):
        """Forward quiz to all groups"""
        try:
            # Get quiz_id from job data
            quiz_id = context.job.data['quiz_id']
            
            if quiz_id not in self.quiz_data:
                logger.error(f"Quiz {quiz_id} not found in quiz_data for forwarding")
                return
            
            quiz_data = self.quiz_data[quiz_id]
            correct_option = quiz_data['correct_option']
            
            # Check if correct answer has been set
            if correct_option == -1:
                logger.warning(f"Quiz {quiz_id} still has no correct answer set. Skipping forwarding.")
                # Send reminder to admin
                await context.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text="⚠️ Quiz not forwarded - Please set correct answer by replying to the quiz!",
                    parse_mode='Markdown'
                )
                return
            
            # Get all active groups
            groups = await db.get_all_groups()
            sent_count = 0
            
            poll = quiz_data['poll_object']
            options = quiz_data['options']
            
            for group in groups:
                if group['id'] != ADMIN_GROUP_ID:  # Don't send back to admin group
                    try:
                        # Send new poll (not forward) with is_anonymous=False
                        sent_message = await context.bot.send_poll(
                            chat_id=group['id'],
                            question=poll.question,
                            options=options,
                            type='quiz',  # Always send as quiz for answer tracking
                            correct_option_id=correct_option,
                            is_anonymous=False,  # Critical: allows us to track user answers
                            explanation=poll.explanation if poll.explanation else "📚 JEE Quiz Bot"
                        )
                        
                        # Store poll mapping for answer tracking
                        self.poll_mapping[sent_message.poll.id] = {
                            'quiz_id': quiz_id,
                            'group_id': group['id'],
                            'message_id': sent_message.message_id
                        }
                        
                        sent_count += 1
                        logger.info(f"✅ Quiz sent to group {group['id']} with poll_id {sent_message.poll.id}")
                        
                    except Exception as e:
                        logger.error(f"❌ Failed to send quiz to group {group['id']}: {e}")
            
            if sent_count > 0:
                # Send confirmation to admin
                option_letter = chr(65 + correct_option)  # Convert to A, B, C, D
                confirmation = f"🎯 **Quiz Forwarded Successfully!**\n\n📊 Sent to {sent_count} groups\n✅ Correct Answer: **{option_letter}**"
                await context.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=confirmation,
                    parse_mode='Markdown'
                )
                logger.info(f"🎯 Quiz '{poll.question[:50]}...' sent to {sent_count} groups successfully!")
            else:
                await context.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text="⚠️ Quiz not sent to any groups - No active groups found!",
                    parse_mode='Markdown'
                )
                logger.warning("⚠️ Quiz not sent to any groups")
        
        except Exception as e:
            logger.error(f"Error forwarding quiz {quiz_id}: {e}")
            await context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=f"❌ Error forwarding quiz: {e}",
                parse_mode='Markdown'
            )
    
    async def handle_reply_to_poll(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle replies to poll messages in admin group to capture correct answers"""
        message = update.message
        chat = update.effective_chat
        
        # Only process replies in admin group
        if chat.id != ADMIN_GROUP_ID:
            return
        
        # Check if this is a reply to a poll message
        reply_to_message = message.reply_to_message
        if not reply_to_message or not reply_to_message.poll:
            return
        
        # Parse the reply text for correct option (a, b, c, d)
        reply_text = message.text.lower().strip()
        
        # Map text options to indices
        option_mapping = {
            'a': 0, '1': 0, 'option a': 0, 'a)': 0,
            'b': 1, '2': 1, 'option b': 1, 'b)': 1,
            'c': 2, '3': 2, 'option c': 2, 'c)': 2,
            'd': 3, '4': 3, 'option d': 3, 'd)': 3,
            'e': 4, '5': 4, 'option e': 4, 'e)': 4,
        }
        
        correct_option_index = None
        for text, index in option_mapping.items():
            if text in reply_text:
                correct_option_index = index
                break
        
        if correct_option_index is None:
            # Send help message
            help_text = """
❌ Invalid format! Please reply with correct option:

✅ **Valid formats:**
• `a` or `A` 
• `b` or `B`
• `c` or `C` 
• `d` or `D`
• `1`, `2`, `3`, `4`

**Example:** Reply to quiz with just: `c`
            """
            await message.reply_text(help_text, parse_mode='Markdown')
            return
        
        # Find the quiz in our stored data using message_id
        poll_message_id = reply_to_message.message_id
        quiz_id_to_update = None
        
        # Search through stored quiz data using message_id for better matching
        for quiz_id, quiz_data in self.quiz_data.items():
            # Match by message_id for precise identification
            if quiz_data.get('message_id') == poll_message_id:
                quiz_id_to_update = quiz_id
                break
        
        if quiz_id_to_update is None:
            await message.reply_text("❌ Could not find the quiz to update. Please try again.")
            return
        
        # Validate the option index against available options
        poll_options_count = len(reply_to_message.poll.options)
        if correct_option_index >= poll_options_count:
            await message.reply_text(f"❌ Invalid option! This quiz only has options A-{chr(65 + poll_options_count - 1)}")
            return
        
        # Update the stored quiz data with correct option
        self.quiz_data[quiz_id_to_update]['correct_option'] = correct_option_index
        
        # Also update in database
        await db.update_quiz_correct_option(quiz_id_to_update, correct_option_index)
        
        # Send confirmation
        option_letter = chr(65 + correct_option_index)  # Convert to A, B, C, D
        confirmation_text = f"✅ **Correct Answer Set!**\n\n🎯 Quiz: {reply_to_message.poll.question[:50]}...\n✅ Correct Option: **{option_letter}**\n\n⏰ **Quiz will be forwarded to all groups in 30 seconds!**"
        
        await message.reply_text(confirmation_text, parse_mode='Markdown')
        logger.info(f"🔧 Admin updated quiz {quiz_id_to_update} correct answer to option {correct_option_index} ({option_letter})")
        
        # Schedule forwarding after 30 seconds
        await self._schedule_quiz_forwarding(quiz_id_to_update, context)
    
    async def handle_poll_answer(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle quiz answers"""
        poll_answer = update.poll_answer
        user = poll_answer.user
        poll_id = poll_answer.poll_id
        selected_options = poll_answer.option_ids
        
        # Get poll mapping data
        if poll_id not in self.poll_mapping:
            return
        
        poll_data = self.poll_mapping[poll_id]
        quiz_id = poll_data['quiz_id']
        group_id = poll_data['group_id']
        
        # Get quiz data
        if quiz_id not in self.quiz_data:
            return
        
        quiz_data = self.quiz_data[quiz_id]
        correct_option = quiz_data['correct_option']
        
        # Determine points
        if len(selected_options) == 0:
            points = 0  # Unattempted
            message_type = "unattempted"
        elif selected_options[0] == correct_option:
            points = 4  # Correct
            message_type = "correct"
        else:
            points = -1  # Wrong
            message_type = "wrong"
        
        # Add user to database if not exists
        await db.add_user(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name
        )
        
        # Add user to group if not exists
        await db.add_group_member(user.id, group_id)
        
        try:
            # Record the answer with proper group_id
            await db.record_quiz_answer(
                user_id=user.id,
                group_id=group_id,
                quiz_id=quiz_id,
                selected_option=selected_options[0] if selected_options else -1,
                points=points
            )
            
            # Send response message to the GROUP (not DM)
            if message_type == "correct":
                response = random.choice(CORRECT_MESSAGES)
                await context.bot.send_message(
                    chat_id=group_id,
                    text=f"🎉 [{user.first_name}](tg://user?id={user.id}) {response}",
                    parse_mode='Markdown'
                )
            elif message_type == "wrong":
                response = random.choice(WRONG_MESSAGES)
                await context.bot.send_message(
                    chat_id=group_id,
                    text=f"😔 [{user.first_name}](tg://user?id={user.id}) {response}",
                    parse_mode='Markdown'
                )
            
            logger.info(f"Quiz answer recorded: User {user.id}, Group: {group_id}, Points: {points}")
            
        except Exception as e:
            logger.error(f"Error recording quiz answer: {e}")
    
    async def refresh_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /refresh command"""
        await update.message.reply_text("🔄 Bot refreshed successfully! All systems operational. 🚀")
    
    async def donate_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /donate command"""
        user = update.effective_user
        
        # Create donation button
        keyboard = [
            [InlineKeyboardButton("💝 𝗗𝗢𝗡𝗔𝗧𝗘 𝗡𝗢𝗪 💝", url="https://t.me/DrQuizDonationBot")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        donate_text = f"""
╔═════════════════════════════════════╗
║  💝 𝗦𝗨𝗣𝗣𝗢𝗥𝗧 𝗢𝗨𝗥 𝗠𝗜𝗦𝗦𝗜𝗢𝗡 💝  ║
╚═════════════════════════════════════╝

🌟 Hey {user.first_name}! ✨

🎯 **Your Support Makes Dreams Come True!**

💡 Every donation helps thousands of JEE students:
✅ Access FREE quality quiz questions daily
✅ Improve their preparation with instant scoring  
✅ Compete with peers in real-time leaderboards
✅ Get closer to their MEDICAL COLLEGE dreams! 🏥

🚀 **Why Your Support Matters:**
🔥 Server hosting & maintenance costs
⚡ Adding new features & improvements  
📚 Creating more educational content
🛡️ Ensuring 100% uptime for students

💖 **We've Created Something Special For You:**

🤖 **Secure Donation Bot:** @DrQuizDonationBot
🔒 **100% Safe & Transparent** transactions
🎁 **Special Recognition** for our supporters  
📊 **Impact Reports** - See how you're helping students!

════════════════════════════════════════

🌈 **"Education is the most powerful weapon which you can use to change the world"** - Nelson Mandela

💝 Your kindness today shapes a doctor's journey tomorrow!

🙏 **Thank you for believing in education and our mission!**
        """
        
        await update.message.reply_text(
            donate_text, 
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def developer_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /developer command"""
        user = update.effective_user
        
        keyboard = [
            [InlineKeyboardButton("💬 Meet With Aman", url="https://t.me/thegodoftgbot")],
            [InlineKeyboardButton("🌟 Follow Updates", url="https://t.me/DrQuizBotUpdates")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        developer_text = f"""
╔═══════════════════════════════════╗
║   🚀 𝗠𝗘𝗘𝗧 𝗧𝗛𝗘 𝗗𝗘𝗩𝗘𝗟𝗢𝗣𝗘𝗥 🚀   ║
╚═══════════════════════════════════╝

👋 Namaste 🇮🇳! ✨

🎯 Meet Aman - The visionary behind this JEE QUIZ BOT

⚡ Who is Aman?
🏢 Founder & CEO of 『Sᴀɴsᴀ Fᴇᴇʟ』
🎓 working On Different Projects. 
💻 Tech Innovator building educational solutions
🏆 very soon going to launch Jee Quiz App with multiple features.  

🌟 What Makes Him Special?
✅ Created this FREE quiz bot for students like you
✅ Personally reviews every feature for student benefit  
✅ Available for 1-on-1 chatting, to know the suggestions ideas and feedback 
✅ Passionate about making JEE preparation affordable

═══════════════════════════════════
Let's connect with Aman Directly, privately and securely!
        """
        
        await update.message.reply_text(
            developer_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def leaderboard_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /leaderboard command - show current group leaderboard"""
        chat = update.effective_chat
        
        # Only works in groups
        if chat.type == 'private':
            await update.message.reply_text(
                "🏆 **Group Leaderboard**\n\n"
                "❌ This command only works in groups!\n"
                "🔄 Please use this command in a group where the bot is active.",
                parse_mode='Markdown'
            )
            return
        
        try:
            # Get group leaderboard data
            group_leaderboard = await db.get_group_leaderboard(chat.id)
            
            if not group_leaderboard:
                no_data_text = """
╔══════════════════════════════════╗
║  🏆 **𝗚𝗥𝗢𝗨𝗣 𝗟𝗘𝗔𝗗𝗘𝗥𝗕𝗢𝗔𝗥𝗗** 🏆  ║
╚══════════════════════════════════╝

📊 **Current Status:** No quiz activity yet!

🎯 **How to get on the leaderboard:**
✅ Answer quiz questions sent by the bot
✅ Earn points: +4 ✅ correct, -1 ❌ wrong, 0 ⭕ unattempted
✅ Compete with other group members

🚀 **Start answering quizzes to see your ranking!**
                """
                await update.message.reply_text(no_data_text, parse_mode='Markdown')
                return
            
            # Build decorated leaderboard message
            group_title = chat.title or "This Group"
            leaderboard_text = f"""
╔══════════════════════════════════╗
║  🏆 **𝗚𝗥𝗢𝗨𝗣 𝗟𝗘𝗔𝗗𝗘𝗥𝗕𝗢𝗔𝗥𝗗** 🏆  ║
╚══════════════════════════════════╝

🏠 **Group:** {group_title}
📅 **Updated:** {datetime.now(TIMEZONE).strftime('%d %b %Y, %I:%M %p')}
⚡ **Total Players:** {len(group_leaderboard)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
            
            # Add top performers with special decorations
            for i, user in enumerate(group_leaderboard[:20], 1):  # Show top 20
                name = user.get('first_name') or user.get('username') or 'Unknown'
                score = user['score']
                correct = user['correct']
                wrong = user['wrong'] 
                unattempted = user['unattempted']
                total_attempted = correct + wrong + unattempted
                
                # Rank emojis and decorations
                if i == 1:
                    rank_emoji = "🥇"
                    decoration = "👑"
                elif i == 2:
                    rank_emoji = "🥈" 
                    decoration = "⭐"
                elif i == 3:
                    rank_emoji = "🥉"
                    decoration = "✨"
                elif i <= 10:
                    rank_emoji = f"🏅 **{i}**"
                    decoration = "🔥"
                else:
                    rank_emoji = f"**{i}**"
                    decoration = "💪"
                
                # Performance indicators
                if score >= 100:
                    performance = "🚀 Master"
                elif score >= 50:
                    performance = "⚡ Expert"
                elif score >= 20:
                    performance = "🎯 Pro"
                elif score >= 10:
                    performance = "📈 Rising"
                else:
                    performance = "🌱 Beginner"
                
                # Accuracy calculation
                if total_attempted > 0:
                    accuracy = round((correct / total_attempted) * 100, 1)
                    if accuracy >= 80:
                        accuracy_emoji = "🎯"
                    elif accuracy >= 60:
                        accuracy_emoji = "📊"
                    else:
                        accuracy_emoji = "📉"
                else:
                    accuracy = 0
                    accuracy_emoji = "📊"
                
                leaderboard_text += f"""
{rank_emoji} [{name}](tg://user?id={user['id']}) {decoration} {performance}

    📊 **Total Score:** {score} points
    🎯 **Questions:** {total_attempted} attempted
    ✅ **Correct:** {correct} | ❌ **Wrong:** {wrong} | ⭕ **Skipped:** {unattempted}
    {accuracy_emoji} **Accuracy:** {accuracy}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
            
            # Add footer with motivational message
            leaderboard_text += f"""

🎯 **Keep practicing to climb higher!**
💡 **Tip:** Answer more quizzes to improve your rank

🏆 Use /leaderboard anytime to check your progress!
            """
            
            await update.message.reply_text(leaderboard_text, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Error in leaderboard command: {e}")
            await update.message.reply_text(
                "❌ Sorry, there was an error fetching the leaderboard. Please try again later.",
                parse_mode='Markdown'
            )
    
    async def broadcast_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /broadcast command (admin only)"""
        user = update.effective_user
        
        # Check if user is admin
        if not await db.is_admin(user.id):
            await update.message.reply_text("❌ You are not authorized to use this command.")
            return
        
        # Check if replying to a message
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Please reply to a message to broadcast it.")
            return
        
        replied_message = update.message.reply_to_message
        
        try:
            # Get all groups and users
            groups = await db.get_all_groups()
            broadcast_count = 0
            
            # Broadcast to all groups
            for group in groups:
                try:
                    await context.bot.copy_message(
                        chat_id=group['id'],
                        from_chat_id=replied_message.chat_id,
                        message_id=replied_message.message_id
                    )
                    broadcast_count += 1
                except Exception as e:
                    logger.error(f"Failed to broadcast to group {group['id']}: {e}")
            
            await update.message.reply_text(
                f"✅ Message broadcast to {broadcast_count} groups successfully!"
            )
            
        except Exception as e:
            logger.error(f"Broadcast error: {e}")
            await update.message.reply_text("❌ Error occurred during broadcast.")
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stats command (admin only)"""
        user = update.effective_user
        
        # Check if user is admin
        if not await db.is_admin(user.id):
            await update.message.reply_text("❌ You are not authorized to use this command.")
            return
        
        try:
            stats = await db.get_bot_stats()
            
            stats_text = f"""
📊 **Bot Statistics**

👥 **Total Users:** {stats['total_users']}
🏢 **Total Groups:** {stats['total_groups']}
❓ **Total Quizzes:** {stats['total_quizzes']}
✏️ **Total Answers:** {stats['total_answers']}

🕒 **Last Updated:** {datetime.now(TIMEZONE).strftime('%Y-%m-%d %H:%M:%S IST')}
            """
            
            await update.message.reply_text(stats_text, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Stats error: {e}")
            await update.message.reply_text("❌ Error fetching statistics.")
    
    async def promote_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /promote command (admin only)"""
        user = update.effective_user
        
        # Check if user is admin
        if not await db.is_admin(user.id):
            await update.message.reply_text("❌ You are not authorized to use this command.")
            return
        
        # Parse user ID from command
        try:
            user_id = int(context.args[0])
        except (IndexError, ValueError):
            await update.message.reply_text("❌ Please provide a valid user ID.\nUsage: /promote <user_id>")
            return
        
        try:
            await db.add_admin(user_id=user_id, promoted_by=user.id)
            await update.message.reply_text(f"✅ User {user_id} has been promoted to admin.")
            
        except Exception as e:
            logger.error(f"Promote error: {e}")
            await update.message.reply_text("❌ Error promoting user.")
    
    async def remove_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /remove command (admin only)"""
        user = update.effective_user
        
        # Check if user is admin
        if not await db.is_admin(user.id):
            await update.message.reply_text("❌ You are not authorized to use this command.")
            return
        
        # Parse user ID from command
        try:
            user_id = int(context.args[0])
        except (IndexError, ValueError):
            await update.message.reply_text("❌ Please provide a valid user ID.\nUsage: /remove <user_id>")
            return
        
        try:
            await db.remove_admin(user_id)
            await update.message.reply_text(f"✅ User {user_id} has been removed from admin list.")
            
        except Exception as e:
            logger.error(f"Remove admin error: {e}")
            await update.message.reply_text("❌ Error removing admin.")
    
    async def adminlist_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /adminlist command (admin only)"""
        user = update.effective_user
        
        # Check if user is admin
        if not await db.is_admin(user.id):
            await update.message.reply_text("❌ You are not authorized to use this command.")
            return
        
        try:
            admins = await db.get_all_admins()
            
            if not admins:
                await update.message.reply_text("👥 No admins found.")
                return
            
            admin_text = "👑 **Current Bot Admins:**\n\n"
            for admin in admins:
                admin_text += f"• **{admin['first_name'] or 'Unknown'}** (@{admin['username'] or 'N/A'})\n"
                admin_text += f"  ID: `{admin['user_id']}`\n"
                admin_text += f"  Since: {admin['created_at'].strftime('%Y-%m-%d')}\n\n"
            
            await update.message.reply_text(admin_text, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Admin list error: {e}")
            await update.message.reply_text("❌ Error fetching admin list.")
    
    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline keyboard callbacks"""
        query = update.callback_query
        await query.answer()
        
        # Handle any callback queries if needed
        logger.info(f"Callback query: {query.data}")
    
    async def send_daily_leaderboards(self, context: ContextTypes.DEFAULT_TYPE = None):
        """Send daily leaderboards at 10:00 PM IST"""
        try:
            groups = await db.get_all_groups()
            
            for group in groups:
                if group['id'] == ADMIN_GROUP_ID:
                    continue  # Skip admin group
                
                try:
                    # Group leaderboard
                    group_leaderboard = await db.get_group_leaderboard(group['id'])
                    
                    if not group_leaderboard:
                        continue
                    
                    group_text = f"🏆 **Daily Group Leaderboard - {group['title']}**\n"
                    group_text += f"📅 Date: {datetime.now(TIMEZONE).strftime('%Y-%m-%d')}\n\n"
                    
                    for i, user in enumerate(group_leaderboard, 1):
                        name = user['first_name'] or 'Unknown'
                        score = user['score']
                        correct = user['correct']
                        wrong = user['wrong']
                        unattempted = user['unattempted']
                        
                        rank_emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
                        
                        group_text += f"{rank_emoji} [{name}](tg://user?id={user['id']}) - {score} pts\n"
                        group_text += f"   ✅ {correct} | ❌ {wrong} | ⭕ {unattempted}\n\n"
                    
                    bot = context.bot if context else self.application.bot
                    await bot.send_message(
                        chat_id=group['id'],
                        text=group_text,
                        parse_mode='Markdown'
                    )
                    
                    # Universal leaderboard
                    universal_leaderboard = await db.get_universal_leaderboard(50)
                    
                    if universal_leaderboard:
                        universal_text = "🌍 **Universal Leaderboard (Top 50)**\n"
                        universal_text += f"📅 Date: {datetime.now(TIMEZONE).strftime('%Y-%m-%d')}\n\n"
                        
                        for i, user in enumerate(universal_leaderboard, 1):
                            name = user['first_name'] or 'Unknown'
                            score = user['score']
                            
                            rank_emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
                            
                            universal_text += f"{rank_emoji} [{name}](tg://user?id={user['id']}) - {score} pts\n"
                        
                        await bot.send_message(
                            chat_id=group['id'],
                            text=universal_text,
                            parse_mode='Markdown'
                        )
                    
                except Exception as e:
                    logger.error(f"Error sending leaderboard to group {group['id']}: {e}")
            
            logger.info("Daily leaderboards sent successfully")
            
        except Exception as e:
            logger.error(f"Error in daily leaderboard task: {e}")
    
    
    async def run(self):
        """Run the bot"""
        try:
            # Initialize
            await self.initialize()
            
            
            # Start the bot
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling()
            
            logger.info("JEE Quiz Bot started successfully!")
            
            # Keep running
            await asyncio.Event().wait()
            
        except Exception as e:
            logger.error(f"Bot error: {e}")
        finally:
            # Cleanup
            if self.application:
                await self.application.stop()

# Main execution
async def main():
    bot = JEEQuizBot()
    await bot.run()

if __name__ == "__main__":
    asyncio.run(main())