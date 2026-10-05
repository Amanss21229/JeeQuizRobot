# JEE Quiz Bot

## Overview
A comprehensive Telegram bot for JEE students featuring automatic quiz forwarding, real-time scoring, daily leaderboards, and complete admin management. Built using Python with python-telegram-bot v20+ and PostgreSQL database.

## Recent Changes
- 2025-12-10: Changed Universal Leaderboard from Weekly to Daily (Last 24 Hours)
  - Daily leaderboard at 10:00 PM IST now shows scores from last 24 hours only
  - New database function: get_daily_universal_leaderboard() calculates scores from answered_at timestamp
  - Message text updated to clearly indicate "Last 24 Hours" instead of weekly scores
  - Both group and private chat leaderboards use daily scores
- 2025-12-10: Added Daily Wrong Quiz Summary Feature
  - New command: /mymistake - View today's wrong answered questions
  - In groups: Shows button to redirect user to private chat
  - In private chat: Displays all wrong quizzes with correct answers
  - Deep link support: /start mymistake for seamless group-to-private redirection
  - Scheduled job at 10:10 PM IST sends daily wrong quiz summary to all users
  - Unique quizzes only (no duplicates) with DISTINCT ON clause
  - Shows question, user's wrong answer, and correct answer for each quiz
  - Hinglish messages for better user engagement
- 2025-12-01: High-Speed Broadcast and Forward System
  - Implemented parallel message sending with asyncio (25 concurrent sends)
  - Broadcast/forward operations now complete in 5-10 minutes instead of 1+ hour
  - Updated all commands: /broadcast, /forward, /pbroadcast, /gbroadcast, /ebroadcast
  - Smart rate limit handling with automatic retry on Telegram 429 errors
  - Real-time progress updates showing percentage completion
  - Final statistics show groups, channels, users counts with time taken
  - Quizzes now sent to both groups AND channels with separate counts
- 2025-11-16: Database Optimizations to Reduce Neon DB Compute Hours
  - Optimized connection pool: min_size=0 (no idle connections), max_size=3 (reduced from 10)
  - Added aggressive connection lifecycle management: 60-second idle timeout, 10-second query timeout
  - Implemented in-memory caching for frequently accessed data (force join groups, language preferences)
  - Cache TTL: 5 minutes for force join groups, 1 minute for language preferences
  - Automatic cache invalidation on data updates
  - Expected reduction: 50-70% decrease in daily compute hours
- 2025-11-16: Added New Broadcasting and Promotion Commands
  - New admin command: /gbroadcast - Broadcast messages to all groups and channels only (not private chats)
  - Supports all message types: text, images, videos, PDFs, polls, buttons, emojis, stickers, GIFs
  - New user command: /promotion - Show promotional message for advertising services
  - Promotional message includes inline button linking to @sansaadsbot
  - Well-formatted promotional text with emojis, borders, and professional design
- 2025-11-12: Added /Myscore Command - Personal Achievement Report Card
  - Beautiful decorated achievement report card showing user's complete quiz performance
  - Clickable user name to view profile
  - Total universal score and rank across all groups
  - Performance badges (MASTER, EXPERT, PRO, RISING, BEGINNER) based on accuracy
  - Detailed quiz statistics (correct, wrong, unattempted)
  - Group-wise performance breakdown with scores, ranks, and accuracy for each group
  - Narrow format with emojis, margins, and borders for easy readability
  - Share button to share achievements on Telegram and other apps
  - Branding: "@JeeQuizRobot Powered By Sansa"
  - Force join protection (admins bypass)
  - MarkdownV2 formatting with proper escaping for special characters
  - Database method to efficiently fetch group-wise scores and ranks
- 2025-11-12: Enhanced Force Join System - Complete Coverage
  - Force join now enforced on ALL user commands and features: /start, /refresh, /donate, /developer, /sol, /leaderboard, /language (private chats), poll answers, and callback queries
  - Admins completely bypass all force join requirements
  - Group admins can manage group settings (like language) without force join
  - Proper force join message with inline buttons shown for all blocked actions
  - Private chat callbacks protected while group functionality remains unaffected
  - No way for users to bypass force join - complete enforcement
- 2025-11-11: Added Force Join System
  - New admin commands: /fjoin and /removefjoin
  - Maximum 5 groups/channels can be required for force join
  - Users must join all required groups before using bot features
  - Polite message with inline buttons to guide users to join
  - Database-backed force join configuration
- 2025-11-11: Added multilingual quiz support (Hindi/English)
  - New command: /language - Select quiz language preference
  - In groups: Only admins can change language (bot admins or group admins)
  - In private chat: All users can select their preferred language
  - Hindi option: Quizzes automatically translated to Hindi using Google Translate
  - English option: Quizzes remain in original English
  - Translation caching for improved performance and reliability
  - All users share same leaderboard regardless of language
  - Automatic fallback to English if translation fails
  - Database-backed language preference per group
- 2025-11-03: Enhanced group leaderboard with universal ranks
  - Shows each user's global rank across all groups
  - Premium decorated design with colorful emojis and clear layout
  - Performance badges (MASTER, EXPERT, PRO, RISING, BEGINNER)
  - Accuracy indicators with visual icons
  - Special borders for top 3 performers
  - Easy-to-read format with organized stats
  - Compatible with Neon PostgreSQL database
- 2025-11-03: Added user-admin messaging system
  - Users can send messages to bot in private chat, automatically forwarded to admin group (-1003049872361)
  - Admins can reply to user messages directly from admin group
  - Supports all message types: text, images, videos, documents, audio, voice, stickers, GIFs, PDFs
  - User details (name, username, ID) shown in admin group
  - Database-backed message mapping for reliability
  - Commands are not forwarded to avoid spam
- 2025-10-12: Added reply toggle system for groups
  - New commands: /replyoff and /replyon (admin/group admin only)
  - Groups can disable/enable quiz reply messages independently
  - Quiz scoring continues to work even when replies are disabled
  - Admin permission check for both bot admins and group admins
  - Database migration added for existing installations
- 2025-10-12: Added custom reply management system
  - New admin commands: /addpositivereply, /addnegativereply, /removereply
  - Support for text and media replies (photos, videos, documents, stickers, audio, voice, GIFs)
  - Custom replies integrated with existing hardcoded messages
- 2025-09-12: Initial bot implementation with all core features
- Database models created with proper schema for users, groups, quizzes, scores, and admins
- Main bot file with complete functionality including start commands, quiz handling, scoring system, and daily leaderboards
- All admin commands implemented: /broadcast, /stats, /promote, /remove, /adminlist
- User commands implemented: /refresh, /donate, /developer with proper formatting

## Project Architecture
```
├── main.py          # Main bot application with all handlers and logic
├── models.py        # Database models and operations using asyncpg
├── requirements.txt # Auto-generated by uv package manager
└── replit.md       # Project documentation
```

## Features Implemented
### Core Functionality
- ✅ Start message with 3 inline buttons (Add to Group, Meet Owner, Join Community)
- ✅ Automatic quiz forwarding from admin group (-1002848830142) to all bot groups
- ✅ Answer checking system with points (+4 correct, -1 wrong, 0 unattempted)
- ✅ Decorated congratulatory and meme-style failure messages with user tagging
- ✅ Daily automated leaderboards at 10:00 PM IST (Group and Universal top 50)

### Admin Commands
- ✅ /broadcast - Forward replied message to all groups and users
- ✅ /pbroadcast - Broadcast message to all private chats only (not groups)
- ✅ /gbroadcast - Broadcast message to all groups and channels only (not private chats)
- ✅ /stats - Show bot statistics (users, groups, quizzes, answers)
- ✅ /promote - Promote user as bot admin
- ✅ /remove - Remove user from bot admin list
- ✅ /adminlist - Show all current bot admins
- ✅ /replyoff - Disable quiz reply messages in a group (bot admin/group admin only)
- ✅ /replyon - Enable quiz reply messages in a group (bot admin/group admin only)
- ✅ /language - Set quiz language to Hindi or English (admin-only in groups, everyone in private)
- ✅ /fjoin - Add groups/channels to force join list (max 5, reply to forwarded message)
- ✅ /removefjoin - Remove group/channel from force join list (reply to forwarded message)

### User Commands
- ✅ /refresh - Reboot/refresh the bot
- ✅ /donate - Donate via Telegram Stars
- ✅ /developer - Meet the developer with inline button
- ✅ /promotion - Get promotional advertising services information
- ✅ /language - Select preferred quiz language (available in private chats)

### Database Schema
- **users**: User profiles with scoring statistics
- **groups**: Active bot groups (with reply toggle and language preference support)
- **admins**: Bot administrators
- **quizzes**: Quiz questions and metadata
- **user_quiz_scores**: Individual quiz responses and scores
- **group_members**: Group membership tracking
- **custom_replies**: Custom text and media replies
- **quiz_solutions**: Quiz solutions storage
- **message_mapping**: User-admin message forwarding mappings
- **force_join_groups**: Required groups/channels for force join (max 5)

## Configuration
- Bot Token: **REQUIRED** - Add your bot token from @BotFather as `BOT_TOKEN` secret
- Admin Group ID: -1002848830142
- Target Channels: @thegodoftgbot, t.me/DrQuizBotUpdates
- Timezone: Asia/Kolkata (IST)
- Default Admin: 6195713937 (thegodoftgbot)

### Setup Instructions
1. **Create Telegram Bot**: Message @BotFather on Telegram and create a new bot
2. **Get Bot Token**: Copy the token provided by @BotFather
3. **Add Secret**: In Replit, add the token as a secret named `BOT_TOKEN`
4. **Run the Bot**: The workflow will automatically start the bot

### Required Environment Variables
- `BOT_TOKEN`: Your Telegram bot token from @BotFather
- `DATABASE_URL`: PostgreSQL database connection (automatically configured by Replit)
- `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`, `PGHOST`: Database connection details (automatically configured)

## Technical Stack
- **Language**: Python 3.11
- **Bot Framework**: python-telegram-bot v22.3
- **Database**: PostgreSQL (via Replit integration)
- **Database Driver**: asyncpg
- **Timezone**: pytz
- **Scheduling**: asyncio-based scheduler for daily leaderboards
- **Translation**: deep-translator (Google Translate backend) for Hindi quiz translation

## User Preferences
- 100% automatic operation (no manual setup required monthly/yearly)
- Multi-group support with efficient handling
- Real-time quiz forwarding and scoring
- Decorated messages with emojis and user tagging
- Daily automated leaderboard posting at 10:00 PM IST

## Deployment Notes
- Bot runs continuously with asyncio event loop
- Database connection pooling for efficient operations
- Background scheduler for daily leaderboard posting
- Error handling and logging throughout the application
- All environment variables properly configured via Replit integrations
