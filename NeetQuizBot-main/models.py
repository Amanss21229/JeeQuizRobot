import asyncio
import asyncpg
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional, List, Dict, Union

# Setup logger
logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None
        # Cache to reduce database queries and compute hours
        self._cache = {}
        self._cache_ttl = {
            'force_join_groups': 300,  # 5 minutes
            'group_language': 60,  # 1 minute per group
            'admin_list': 300,  # 5 minutes
        }
    
    def _get_cache(self, key: str) -> Optional[any]:
        """Get value from cache if not expired"""
        if key in self._cache:
            data, timestamp, ttl = self._cache[key]
            if time.time() - timestamp < ttl:
                return data
            else:
                del self._cache[key]
        return None
    
    def _set_cache(self, key: str, value: any, ttl: int):
        """Set value in cache with TTL"""
        self._cache[key] = (value, time.time(), ttl)
    
    def _invalidate_cache(self, key: str):
        """Invalidate specific cache key"""
        if key in self._cache:
            del self._cache[key]
    
    async def init_pool(self):
        """Initialize database connection pool with aggressive timeouts to reduce Neon DB compute hours"""
        self.pool = await asyncpg.create_pool(
            os.environ.get("DATABASE_URL"),
            min_size=0,  # No minimum connections = no idle connections eating compute hours
            max_size=3,  # Reduced from 10 to 3 for lower resource usage
            max_queries=5000,  # Recycle connections after 5000 queries
            max_inactive_connection_lifetime=60.0,  # Close connections idle for 60 seconds
            command_timeout=10.0,  # 10 second timeout for queries
            statement_cache_size=0  # Disable statement cache to reduce memory
        )
        await self.create_tables()
    
    async def create_tables(self):
        """Create all necessary tables"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            # Users table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    total_score INTEGER DEFAULT 0,
                    correct_answers INTEGER DEFAULT 0,
                    wrong_answers INTEGER DEFAULT 0,
                    unattempted INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)
            
            # Groups table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS groups (
                    id BIGINT PRIMARY KEY,
                    title TEXT,
                    type TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    replies_enabled BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)
            
            # Migration: Add replies_enabled column if it doesn't exist (for existing databases)
            await conn.execute("""
                DO $$ 
                BEGIN 
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name='groups' AND column_name='replies_enabled'
                    ) THEN
                        ALTER TABLE groups ADD COLUMN replies_enabled BOOLEAN DEFAULT TRUE;
                    END IF;
                END $$;
            """)
            
            # Migration: Add language_preference column if it doesn't exist (for existing databases)
            await conn.execute("""
                DO $$ 
                BEGIN 
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name='groups' AND column_name='language_preference'
                    ) THEN
                        ALTER TABLE groups ADD COLUMN language_preference TEXT DEFAULT 'english';
                    END IF;
                END $$;
            """)

            # Migration: Add language_preference column to users table if it doesn't exist
            await conn.execute("""
                DO $$ 
                BEGIN 
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name='users' AND column_name='language_preference'
                    ) THEN
                        ALTER TABLE users ADD COLUMN language_preference TEXT DEFAULT 'english';
                    END IF;
                END $$;
            """)

            # Migration: Add clone_bot_id to groups
            await conn.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='groups' AND column_name='clone_bot_id'
                    ) THEN
                        ALTER TABLE groups ADD COLUMN clone_bot_id BIGINT DEFAULT NULL;
                    END IF;
                END $$;
            """)

            # Migration: Add clone_bot_id to users
            await conn.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='users' AND column_name='clone_bot_id'
                    ) THEN
                        ALTER TABLE users ADD COLUMN clone_bot_id BIGINT DEFAULT NULL;
                    END IF;
                END $$;
            """)

            # Migration: Add username to groups (for clickable group links)
            await conn.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='groups' AND column_name='username'
                    ) THEN
                        ALTER TABLE groups ADD COLUMN username TEXT DEFAULT NULL;
                    END IF;
                END $$;
            """)

            # Clone bots table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS clone_bots (
                    id SERIAL PRIMARY KEY,
                    bot_token TEXT UNIQUE NOT NULL,
                    bot_id BIGINT UNIQUE NOT NULL,
                    bot_name TEXT,
                    bot_username TEXT,
                    owner_id BIGINT NOT NULL,
                    owner_username TEXT,
                    owner_name TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    is_paused BOOLEAN DEFAULT FALSE,
                    pause_reason TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # Poll mappings table (for clone bots to look up quiz data)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS poll_mappings (
                    poll_id TEXT PRIMARY KEY,
                    quiz_id INTEGER,
                    group_id BIGINT,
                    message_id BIGINT,
                    clone_bot_id BIGINT,
                    correct_option INTEGER,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # Clone pending setup table (persists across bot restarts)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS clone_pending (
                    user_id BIGINT PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # Admins table (create before inserting data)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    promoted_by BIGINT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # ✅ Ensure default owner is always admin
            await conn.execute("""
                INSERT INTO admins (user_id, username, first_name, promoted_by)
                VALUES (8518377976, 'FounderOfLET', 'Aman', 8518377976)
                ON CONFLICT (user_id) DO NOTHING
            """)
            
            # Quizzes table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS quizzes (
                    id SERIAL PRIMARY KEY,
                    message_id BIGINT,
                    from_group_id BIGINT,
                    quiz_text TEXT,
                    correct_option INTEGER,
                    options JSONB,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)


            
            # User scores per quiz
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS user_quiz_scores (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    group_id BIGINT,
                    quiz_id INTEGER REFERENCES quizzes(id),
                    selected_option INTEGER,
                    points INTEGER,
                    answered_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(user_id, quiz_id, group_id)
                )
            """)
            
            # Group members table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS group_members (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    group_id BIGINT,
                    joined_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(user_id, group_id)
                )
            """)

            # Quiz Solutions table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS quiz_solutions (
                    quiz_id INT PRIMARY KEY REFERENCES quizzes(id),
                    solution_type TEXT,
                    solution_content TEXT,
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)
            
            # Custom Replies table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS custom_replies (
                    id SERIAL PRIMARY KEY,
                    reply_type TEXT NOT NULL,
                    message_type TEXT NOT NULL,
                    content TEXT,
                    file_id TEXT,
                    caption TEXT,
                    added_by BIGINT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            
            # Message Mapping table for user-admin communication
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS message_mapping (
                    forwarded_message_id BIGINT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            
            # Force Join Groups table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS force_join_groups (
                    id SERIAL PRIMARY KEY,
                    chat_id BIGINT UNIQUE,
                    chat_username TEXT,
                    chat_title TEXT,
                    chat_type TEXT,
                    invite_link TEXT,
                    added_by BIGINT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            
            # Sent Messages table for tracking broadcast messages (for /delete command)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS sent_messages (
                    id SERIAL PRIMARY KEY,
                    original_message_id BIGINT NOT NULL,
                    original_chat_id BIGINT NOT NULL,
                    sent_message_id BIGINT NOT NULL,
                    sent_chat_id BIGINT NOT NULL,
                    sent_by BIGINT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            
            # Custom Button Posts table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS button_posts (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    text TEXT NOT NULL,
                    buttons JSONB NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            
            # Create index for faster lookups
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_button_posts_user 
                ON button_posts(user_id)
            """)

                # ============================================================
            # PRIVATE AI SYSTEM TABLES
            # ============================================================

            # AI user profile and long-term personalization memory
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_profiles (
                    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    preferred_name TEXT,
                    age INTEGER,
                    city TEXT,
                    gender TEXT,
                    study_class TEXT,
                    exam_target TEXT,
                    goals TEXT,
                    preferences TEXT,
                    memory_summary TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # AI credit balance
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_credits (
                    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    balance INTEGER NOT NULL DEFAULT 0,
                    total_earned BIGINT NOT NULL DEFAULT 0,
                    total_used BIGINT NOT NULL DEFAULT 0,
                    last_bonus_at TIMESTAMP NULL,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW(),
                    CONSTRAINT ai_credits_balance_nonnegative
                        CHECK (balance >= 0)
                )
            """)

            # Every credit addition/deduction is recorded here
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_credit_transactions (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    amount INTEGER NOT NULL,
                    transaction_type TEXT NOT NULL,
                    balance_after INTEGER NOT NULL,
                    admin_id BIGINT NULL,
                    metadata JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # Active AI chat sessions
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_sessions (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    started_at TIMESTAMP DEFAULT NOW(),
                    last_activity_at TIMESTAMP DEFAULT NOW(),
                    last_billed_at TIMESTAMP DEFAULT NOW(),
                    credits_consumed INTEGER NOT NULL DEFAULT 0,
                    active BOOLEAN NOT NULL DEFAULT TRUE
                )
            """)

            # Persistent reminders/tasks created by users
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_tasks (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    task_text TEXT NOT NULL,
                    schedule_type TEXT NOT NULL,
                    schedule_data JSONB NOT NULL DEFAULT '{}'::jsonb,
                    timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
                    next_run_at TIMESTAMP NULL,
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # Mapping between a Telegram user and their Activity GC forum topic
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_activity (
                    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    activity_group_id BIGINT NOT NULL,
                    topic_id BIGINT NOT NULL,
                    topic_name TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # Useful indexes
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_credit_transactions_user
                ON ai_credit_transactions(user_id, created_at DESC)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_sessions_user_active
                ON ai_sessions(user_id, active)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_tasks_user_active
                ON ai_tasks(user_id, active)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_tasks_next_run
                ON ai_tasks(next_run_at)
                WHERE active = TRUE
            """)

            # ============================================================
            # AIRA — OWNER PERSONAL ASSISTANT
            # ============================================================
            #
            # Completely isolated from:
            # - Private AI
            # - ai_activity
            # - Manual quiz system
            # - Auto Quiz system
            # - Clone bots
            #
            # Phase 1 only creates persistent state.
            # No personal messages are processed here.
            # ============================================================

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS aira_settings (
                    id SMALLINT PRIMARY KEY DEFAULT 1,
                    mode TEXT NOT NULL DEFAULT 'online'
                        CHECK (mode IN ('online', 'offline')),
                    updated_by BIGINT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW(),

                    CONSTRAINT aira_settings_single_row
                        CHECK (id = 1)
                )
            """)

            # Always guarantee exactly one settings row.
            # Existing mode is NEVER overwritten on restart.
            await conn.execute("""
                INSERT INTO aira_settings (
                    id,
                    mode
                )
                VALUES (
                    1,
                    'online'
                )
                ON CONFLICT (id) DO NOTHING
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS aira_users (
                    user_id BIGINT PRIMARY KEY,

                    first_name TEXT,
                    last_name TEXT,
                    username TEXT,

                    topic_id BIGINT,
                    topic_name TEXT,

                    last_incoming_at TIMESTAMP,
                    last_owner_message_at TIMESTAMP,
                    last_auto_reply_at TIMESTAMP,
                    last_urgent_reply_at TIMESTAMP,
                    
                    conversation_started_at TIMESTAMP,
                    offline_reply_stage SMALLINT NOT NULL DEFAULT 0,

                    first_seen_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)

            await conn.execute("""
                ALTER TABLE aira_users
                ADD COLUMN IF NOT EXISTS conversation_started_at TIMESTAMP
            """)

            await conn.execute("""
                ALTER TABLE aira_users
                ADD COLUMN IF NOT EXISTS offline_reply_stage SMALLINT
                NOT NULL DEFAULT 0
            """)        

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_aira_users_topic
                ON aira_users(topic_id)
                WHERE topic_id IS NOT NULL
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_aira_users_last_incoming
                ON aira_users(last_incoming_at DESC)
            """)            

            # ============================================================
            # AUTO QUIZ SOURCE BANK
            # ============================================================

            # Stores quiz polls captured from Biology/Chemistry/Physics
            # source channels. This table is completely independent from
            # the existing manual quiz system.
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS auto_quiz_bank (
                    id BIGSERIAL PRIMARY KEY,

                    subject TEXT NOT NULL
                        CHECK (subject IN ('biology', 'chemistry', 'physics')),

                    source_chat_id BIGINT NOT NULL,
                    source_message_id BIGINT NOT NULL,
                    source_poll_id TEXT,

                    question TEXT NOT NULL,
                    options JSONB NOT NULL,

                    correct_option INTEGER NULL,

                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN (
                            'pending',
                            'ready',
                            'disabled'
                        )),

                    cycle_number INTEGER NOT NULL DEFAULT 1,
                    last_sent_cycle INTEGER NOT NULL DEFAULT 0,

                    total_times_sent BIGINT NOT NULL DEFAULT 0,
                    last_sent_at TIMESTAMP NULL,

                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW(),

                    UNIQUE(source_chat_id, source_message_id)
                )
            """)

            # Scoring bridge: every automatic source quiz gets one normal
            # quizzes.id so the existing score/leaderboard foreign keys remain valid.
            await conn.execute("""
                ALTER TABLE auto_quiz_bank
                ADD COLUMN IF NOT EXISTS scoring_quiz_id INTEGER NULL
                    REFERENCES quizzes(id) ON DELETE SET NULL
            """)

            await conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_auto_quiz_scoring_quiz
                ON auto_quiz_bank(scoring_quiz_id)
                WHERE scoring_quiz_id IS NOT NULL
            """)

            # Stores persistent scheduler state.
            # This survives Render restarts/redeployments.
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS auto_quiz_scheduler_state (
                    id SMALLINT PRIMARY KEY DEFAULT 1,

                    enabled BOOLEAN NOT NULL DEFAULT TRUE,

                    current_subject TEXT NOT NULL DEFAULT 'biology'
                        CHECK (
                            current_subject IN (
                                'biology',
                                'chemistry',
                                'physics'
                            )
                        ),

                    last_slot_key TEXT NULL,
                    last_run_at TIMESTAMP NULL,

                    updated_at TIMESTAMP DEFAULT NOW(),

                    CONSTRAINT auto_quiz_scheduler_single_row
                        CHECK (id = 1)
                )
            """)

            # Always ensure the scheduler has exactly its default state row.
            await conn.execute("""
                INSERT INTO auto_quiz_scheduler_state (
                    id,
                    enabled,
                    current_subject
                )
                VALUES (
                    1,
                    TRUE,
                    'biology'
                )
                ON CONFLICT (id) DO NOTHING
            """)

            # Fast lookup for random READY quizzes of a subject.
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_auto_quiz_bank_subject_ready
                ON auto_quiz_bank(subject, status)
                WHERE status = 'ready'
            """)

            # Helps the no-repeat cycle selector.
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_auto_quiz_bank_cycle
                ON auto_quiz_bank(
                    subject,
                    cycle_number,
                    last_sent_cycle
                )
                WHERE status = 'ready'
            """)

            # ============================================================
            # AUTO QUIZ NO-REPEAT + RELIABLE SLOT STATE
            # ============================================================

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS auto_quiz_subject_state (
                    subject TEXT PRIMARY KEY
                        CHECK (
                            subject IN (
                                'biology',
                                'chemistry',
                                'physics'
                            )
                        ),

                    current_cycle INTEGER NOT NULL DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)

            await conn.execute("""
                INSERT INTO auto_quiz_subject_state (
                    subject,
                    current_cycle
                )
                VALUES
                    ('biology', 1),
                    ('chemistry', 1),
                    ('physics', 1)
                ON CONFLICT (subject) DO NOTHING
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS auto_quiz_runs (
                    slot_key TEXT PRIMARY KEY,

                    subject TEXT NOT NULL
                        CHECK (
                            subject IN (
                                'biology',
                                'chemistry',
                                'physics'
                            )
                        ),
                    
                    quiz_id BIGINT NULL
                        REFERENCES auto_quiz_bank(id)
                        ON DELETE SET NULL,

                    status TEXT NOT NULL DEFAULT 'claimed'
                        CHECK ( 
                            status IN (
                                'claimed',
                                'sent',
                                'failed',
                                'skipped'
                            )
                        ),
                        
                    error_text TEXT NULL,
                    claimed_at TIMESTAMP DEFAULT NOW(),
                    completed_at TIMESTAMP NULL,
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)
            
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_auto_quiz_runs_status
                ON auto_quiz_runs(status, claimed_at)
            """)

            # Fast source-poll lookup when Telegram sends poll updates.
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_auto_quiz_bank_poll
                ON auto_quiz_bank(source_poll_id)
                WHERE source_poll_id IS NOT NULL
            """)    
    
    async def add_user(self, user_id: int, username: Optional[str] = None, first_name: Optional[str] = None, last_name: Optional[str] = None, clone_bot_id: Optional[int] = None):
        """Add or update user in database"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO users (id, username, first_name, last_name, clone_bot_id, updated_at)
                VALUES ($1, $2, $3, $4, $5, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    username = $2,
                    first_name = $3,
                    last_name = $4,
                    clone_bot_id = COALESCE(users.clone_bot_id, EXCLUDED.clone_bot_id),
                    updated_at = NOW()
            """, user_id, username, first_name, last_name, clone_bot_id)
    
    async def get_user(self, user_id: int) -> Optional[Dict]:
        """Get user data by user ID"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT id, username, first_name, last_name, 
                       total_score, correct_answers, wrong_answers, unattempted,
                       created_at, updated_at
                FROM users
                WHERE id = $1
            """, user_id)
            return dict(row) if row else None

        # ============================================================
    # PRIVATE AI PROFILE METHODS
    # ============================================================

    async def get_ai_profile(self, user_id: int) -> Optional[Dict]:
        """Get private AI profile for a user."""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    user_id,
                    preferred_name,
                    age,
                    city,
                    gender,
                    study_class,
                    exam_target,
                    goals,
                    preferences,
                    memory_summary,
                    created_at,
                    updated_at
                FROM ai_profiles
                WHERE user_id = $1
            """, user_id)

            return dict(row) if row else None

    async def create_ai_profile(self, user_id: int) -> Dict:
        """Create an empty AI profile if one does not already exist."""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO ai_profiles (user_id)
                VALUES ($1)
                ON CONFLICT (user_id) DO NOTHING
                RETURNING *
            """, user_id)

            if row:
                return dict(row)

            existing = await conn.fetchrow("""
                SELECT *
                FROM ai_profiles
                WHERE user_id = $1
            """, user_id)

            return dict(existing)

    async def update_ai_profile(
        self,
        user_id: int,
        preferred_name: Optional[str] = None,
        age: Optional[int] = None,
        city: Optional[str] = None,
        gender: Optional[str] = None,
        study_class: Optional[str] = None,
        exam_target: Optional[str] = None,
        goals: Optional[str] = None,
        preferences: Optional[str] = None,
        memory_summary: Optional[str] = None
    ):
        """Update only supplied AI profile fields."""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        await self.create_ai_profile(user_id)

        updates = []
        values = []
        index = 1

        fields = {
            "preferred_name": preferred_name,
            "age": age,
            "city": city,
            "gender": gender,
            "study_class": study_class,
            "exam_target": exam_target,
            "goals": goals,
            "preferences": preferences,
            "memory_summary": memory_summary,
        }

        for field, value in fields.items():
            if value is not None:
                updates.append(f"{field} = ${index}")
                values.append(value)
                index += 1

        if not updates:
            return

        values.append(user_id)

        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""
                UPDATE ai_profiles
                SET {", ".join(updates)},
                    updated_at = NOW()
                WHERE user_id = ${index}
                """,
                *values
            )

    async def clear_ai_profile_memory(
        self,
        user_id: int
    ) -> None:
        """
        Clear all non-sensitive Private AI personalization
        controlled by the memory system.
        """

        if not self.pool:
            raise RuntimeError(
                "Database pool not initialized"
            )

        await self.create_ai_profile(
            user_id
        )

        async with self.pool.acquire() as conn:

            await conn.execute("""
                UPDATE ai_profiles
                SET
                    preferred_name = NULL,
                    age = NULL,
                    city = NULL,
                    gender = NULL,
                    study_class = NULL,
                    exam_target = NULL,
                    goals = NULL,
                    preferences = NULL,
                    memory_summary = NULL,
                    updated_at = NOW()
                WHERE user_id = $1
            """, user_id)


    async def clear_ai_profile_field(
        self,
        user_id: int,
        field: str
    ) -> None:
        """
        Clear one allowed Private AI memory field.
        """

        allowed_fields = {
            "preferred_name",
            "age",
            "city",
            "gender",
            "study_class",
            "exam_target",
            "goals",
            "preferences",
            "memory_summary",
        }

        if field not in allowed_fields:
            raise ValueError(
                "Unsupported AI memory field"
            )

        if not self.pool:
            raise RuntimeError(
                "Database pool not initialized"
            )

        await self.create_ai_profile(
            user_id
        )

        async with self.pool.acquire() as conn:

            await conn.execute(
                f"""
                UPDATE ai_profiles
                SET
                    {field} = NULL,
                    updated_at = NOW()
                WHERE user_id = $1
                """,
                user_id
            )    

        # ============================================================
    # PRIVATE AI CREDIT METHODS
    # ============================================================

    async def get_ai_credits(self, user_id: int) -> Optional[Dict]:
        """Get current AI credit information."""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    user_id,
                    balance,
                    total_earned,
                    total_used,
                    last_bonus_at,
                    created_at,
                    updated_at
                FROM ai_credits
                WHERE user_id = $1
            """, user_id)

            return dict(row) if row else None

    async def initialize_ai_credits(
        self,
        user_id: int,
        initial_credits: int = 30
    ) -> bool:
        """
        Give the one-time new-user AI credit gift.

        Returns True only when the row was created.
        Existing users receive nothing.
        """
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow("""
                    INSERT INTO ai_credits (
                        user_id,
                        balance,
                        total_earned
                    )
                    VALUES ($1, $2::BIGINT, $2::BIGINT)
                    ON CONFLICT (user_id) DO NOTHING
                    RETURNING user_id, balance
                """, user_id, initial_credits)

                if not row:
                    return False

                await conn.execute("""
                    INSERT INTO ai_credit_transactions (
                        user_id,
                        amount,
                        transaction_type,
                        balance_after,
                        metadata
                    )
                    VALUES ($1, $2, 'WELCOME', $2, $3::jsonb)
                """,
                    user_id,
                    initial_credits,
                    json.dumps({
                        "reason": "new_user_welcome_gift"
                    })
                )

                return True

    async def get_last_bonus_at(
        self,
        user_id: int
    ) -> Optional[datetime]:
        """Return the last daily bonus claim time."""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                SELECT last_bonus_at
                FROM ai_credits
                WHERE user_id = $1
            """, user_id)

    async def claim_ai_daily_bonus(
        self,
        user_id: int,
        amount: int
    ) -> Optional[Dict]:
        """
        Atomically claim the daily AI bonus.

        Returns updated credit information when successful.
        Returns None if the 24-hour cooldown has not expired.
        """
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        if amount <= 0:
            raise ValueError("Bonus amount must be greater than zero")

        async with self.pool.acquire() as conn:
            async with conn.transaction():

                row = await conn.fetchrow("""
                    UPDATE ai_credits
                    SET
                        balance = balance + $2,
                        total_earned = total_earned + $2,
                        last_bonus_at = NOW(),
                        updated_at = NOW()
                    WHERE user_id = $1
                      AND (
                          last_bonus_at IS NULL
                          OR last_bonus_at <= NOW() - INTERVAL '24 hours'
                      )
                    RETURNING
                        user_id,
                        balance,
                        total_earned,
                        total_used,
                        last_bonus_at
                """, user_id, amount)

                if not row:
                    return None

                await conn.execute("""
                    INSERT INTO ai_credit_transactions (
                        user_id,
                        amount,
                        transaction_type,
                        balance_after,
                        metadata
                    )
                    VALUES ($1, $2, 'DAILY_BONUS', $3, $4::jsonb)
                """,
                    user_id,
                    amount,
                    row["balance"],
                    json.dumps({
                        "source": "daily_bonus"
                    })
                )

                return dict(row)
                
    async def add_ai_credits(
        self,
        user_id: int,
        amount: int,
        transaction_type: str,
        admin_id: Optional[int] = None,
        metadata: Optional[Dict] = None
    ) -> Optional[Dict]:
        """
        Atomically add AI credits and create an audit transaction.
        """
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        if amount <= 0:
            raise ValueError("Credit amount must be greater than zero")

        metadata = metadata or {}

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Ensure credit row exists.
                await conn.execute("""
                    INSERT INTO ai_credits (user_id)
                    VALUES ($1)
                    ON CONFLICT (user_id) DO NOTHING
                """, user_id)

                row = await conn.fetchrow("""
                    UPDATE ai_credits
                    SET
                        balance = balance + $2,
                        total_earned = total_earned + $2,
                        updated_at = NOW()
                    WHERE user_id = $1
                    RETURNING
                        user_id,
                        balance,
                        total_earned,
                        total_used,
                        last_bonus_at
                """, user_id, amount)

                if not row:
                    return None

                await conn.execute("""
                    INSERT INTO ai_credit_transactions (
                        user_id,
                        amount,
                        transaction_type,
                        balance_after,
                        admin_id,
                        metadata
                    )
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                """,
                    user_id,
                    amount,
                    transaction_type,
                    row["balance"],
                    admin_id,
                    json.dumps(metadata)
                )

                return dict(row)

    async def deduct_ai_credits(
        self,
        user_id: int,
        amount: int,
        transaction_type: str = "ADMIN_ADJUSTMENT",
        admin_id: Optional[int] = None,
        metadata: Optional[Dict] = None
    ) -> Optional[Dict]:
        """
        Atomically deduct credits without allowing
        the user's balance to become negative.
        """

        if not self.pool:
            raise RuntimeError(
                "Database pool not initialized"
            )

        if amount <= 0:
            raise ValueError(
                "Credit amount must be greater than zero"
            )

        metadata = metadata or {}

        async with self.pool.acquire() as conn:
            async with conn.transaction():

                row = await conn.fetchrow("""
                    UPDATE ai_credits
                    SET
                        balance = balance - $2,
                        total_used = total_used + $2,
                        updated_at = NOW()
                    WHERE user_id = $1
                      AND balance >= $2
                    RETURNING
                        user_id,
                        balance,
                        total_earned,
                        total_used,
                        last_bonus_at
                """,
                    user_id,
                    amount
                )

                if not row:
                    return None

                await conn.execute("""
                    INSERT INTO ai_credit_transactions (
                        user_id,
                        amount,
                        transaction_type,
                        balance_after,
                        admin_id,
                        metadata
                    )
                    VALUES (
                        $1,
                        $2,
                        $3,
                        $4,
                        $5,
                        $6::jsonb
                    )
                """,
                    user_id,
                    -amount,
                    transaction_type,
                    row["balance"],
                    admin_id,
                    json.dumps(metadata)
                )

                return dict(row)    

    async def consume_ai_credit(
        self,
        user_id: int,
        amount: int = 1,
        transaction_type: str = "AI_USAGE",
        metadata: Optional[Dict] = None
    ) -> Optional[Dict]:
        """
        Atomically consume AI credits.

        Returns the updated balance information when successful.
        Returns None when insufficient credits.
        """
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        if amount <= 0:
            raise ValueError("Credit amount must be greater than zero")

        metadata = metadata or {}

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow("""
                    UPDATE ai_credits
                    SET
                        balance = balance - $2,
                        total_used = total_used + $2,
                        updated_at = NOW()
                    WHERE user_id = $1
                      AND balance >= $2
                    RETURNING
                        user_id,
                        balance,
                        total_earned,
                        total_used,
                        last_bonus_at
                """, user_id, amount)

                if not row:
                    return None

                await conn.execute("""
                    INSERT INTO ai_credit_transactions (
                        user_id,
                        amount,
                        transaction_type,
                        balance_after,
                        metadata
                    )
                    VALUES ($1, $2, $3, $4, $5::jsonb)
                """,
                    user_id,
                    -amount,
                    transaction_type,
                    row["balance"],
                    json.dumps(metadata)
                )

                return dict(row)

        # ============================================================
    # PRIVATE AI SESSION / MINUTE BILLING METHODS
    # ============================================================

    async def create_ai_session(
        self,
        user_id: int
    ) -> Dict:
        """Create a new active AI chat session for a user."""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO ai_sessions (
                    user_id,
                    started_at,
                    last_activity_at,
                    last_billed_at,
                    credits_consumed,
                    active
                )
                VALUES (
                    $1,
                    NOW(),
                    NOW(),
                    NOW(),
                    0,
                    TRUE
                )
                RETURNING
                    id,
                    user_id,
                    started_at,
                    last_activity_at,
                    last_billed_at,
                    credits_consumed,
                    active
            """, user_id)

            return dict(row)

    async def get_active_ai_session(
        self,
        user_id: int
    ) -> Optional[Dict]:
        """Return the user's currently active AI session."""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    id,
                    user_id,
                    started_at,
                    last_activity_at,
                    last_billed_at,
                    credits_consumed,
                    active
                FROM ai_sessions
                WHERE user_id = $1
                  AND active = TRUE
                ORDER BY id DESC
                LIMIT 1
            """, user_id)

            return dict(row) if row else None

    async def get_all_active_ai_sessions(
        self
    ) -> List[Dict]:
        """Get all currently active AI sessions."""
        if not self.pool:
            raise RuntimeError(
                "Database pool not initialized"
            )

        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    id,
                    user_id,
                    started_at,
                    last_activity_at,
                    last_billed_at,
                    credits_consumed,
                    active
                FROM ai_sessions
                WHERE active = TRUE
                ORDER BY id
            """)

            return [
                dict(row)
                for row in rows
            ]    

    async def touch_ai_session(
        self,
        session_id: int
    ) -> bool:
        """Update the last activity timestamp of an active session."""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE ai_sessions
                SET
                    last_activity_at = NOW()
                WHERE id = $1
                  AND active = TRUE
            """, session_id)

            return result.endswith("1")

    async def bill_ai_session(
        self,
        session_id: int,
        inactivity_minutes: int = 5
    ) -> Optional[Dict]:
        """
        Atomically bill completed active chat minutes.

        One credit = one active chat minute.

        Billing is capped at the inactivity window so that a user
        cannot accumulate charges while they are away from the chat.

        Returns billing information.
        """
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        if inactivity_minutes <= 0:
            raise ValueError(
                "inactivity_minutes must be greater than zero"
            )

        async with self.pool.acquire() as conn:
            async with conn.transaction():

                session = await conn.fetchrow("""
                    SELECT
                        id,
                        user_id,
                        started_at,
                        last_activity_at,
                        last_billed_at,
                        credits_consumed,
                        active
                    FROM ai_sessions
                    WHERE id = $1
                    FOR UPDATE
                """, session_id)

                if not session or not session["active"]:
                    return None

                # ------------------------------------------------
                # Calculate ACTUAL ACTIVE time.
                #
                # Important:
                # Do NOT bill time after the user's last activity.
                # This prevents charging users while they are away.
                # ------------------------------------------------

                elapsed_active_seconds = await conn.fetchval("""
                    SELECT GREATEST(
                        0,
                        EXTRACT(
                            EPOCH FROM (
                                LEAST(
                                    NOW(),
                                    $2::timestamp
                                )
                                - $1::timestamp
                            )
                        )
                    )
                """,
                    session["last_billed_at"],
                    session["last_activity_at"]
                )

                elapsed_active_seconds = float(
                    elapsed_active_seconds or 0
                )

                # Convert only completed active time into credits.
                #
                # We already stop at last_activity_at above,
                # therefore idle time is never included.
                completed_minutes = int(
                    elapsed_active_seconds // 60
                )

                # Nothing to bill yet.
                if completed_minutes <= 0:
                    balance = await conn.fetchval("""
                        SELECT balance
                        FROM ai_credits
                        WHERE user_id = $1
                    """, session["user_id"])

                    return {
                        "billed_minutes": 0,
                        "credits_consumed": int(
                            session["credits_consumed"]
                        ),
                        "balance": int(balance or 0),
                        "session_active": True
                    }

                # Lock the user's credit row.
                balance = await conn.fetchval("""
                    SELECT balance
                    FROM ai_credits
                    WHERE user_id = $1
                    FOR UPDATE
                """, session["user_id"])

                balance = int(balance or 0)

                # We cannot spend more credits than available.
                billable_minutes = min(
                    completed_minutes,
                    balance
                )

                if billable_minutes > 0:

                    new_balance = (
                        balance - billable_minutes
                    )

                    # Deduct credits.
                    await conn.execute("""
                        UPDATE ai_credits
                        SET
                            balance = $2,
                            total_used =
                                total_used + $3,
                            updated_at = NOW()
                        WHERE user_id = $1
                    """,
                        session["user_id"],
                        new_balance,
                        billable_minutes
                    )

                    # Audit transaction.
                    await conn.execute("""
                        INSERT INTO ai_credit_transactions (
                            user_id,
                            amount,
                            transaction_type,
                            balance_after,
                            metadata
                        )
                        VALUES (
                            $1,
                            $2,
                            'AI_USAGE',
                            $3,
                            $4::jsonb
                        )
                    """,
                        session["user_id"],
                        -billable_minutes,
                        new_balance,
                        json.dumps({
                            "source": "private_ai",
                            "session_id": session_id,
                            "billing_unit": "active_minute"
                        })
                    )

                    # Advance billing timestamp only by the amount
                    # actually billed.
                    await conn.execute("""
                        UPDATE ai_sessions
                        SET
                            last_billed_at =
                                last_billed_at
                                + (
                                    $2 * INTERVAL '1 minute'
                                ),
                            credits_consumed =
                                credits_consumed + $2
                        WHERE id = $1
                    """,
                        session_id,
                        billable_minutes
                    )

                # If the user has no credits left, immediately close
                # the session.
                session_active = (
                    balance - billable_minutes > 0
                )

                if not session_active:
                    await conn.execute("""
                        UPDATE ai_sessions
                        SET
                            active = FALSE
                        WHERE id = $1
                    """, session_id)

                return {
                    "billed_minutes": billable_minutes,
                    "credits_consumed": (
                        int(session["credits_consumed"])
                        + billable_minutes
                    ),
                    "balance": (
                        balance - billable_minutes
                    ),
                    "session_active": session_active
                }

    async def close_ai_session(
        self,
        session_id: int
    ) -> bool:
        """Close an active AI session."""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE ai_sessions
                SET
                    active = FALSE
                WHERE id = $1
                  AND active = TRUE
            """, session_id)

            return result.endswith("1")
    # ============================================================
    # PRIVATE AI TELEGRAM ACTIVITY METHODS
    # ============================================================

    async def get_ai_activity(self, user_id: int) -> Optional[Dict]:
        """Get the Activity GC topic mapped to a user."""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    user_id,
                    activity_group_id,
                    topic_id,
                    topic_name,
                    created_at,
                    updated_at
                FROM ai_activity
                WHERE user_id = $1
            """, user_id)

            return dict(row) if row else None

    async def save_ai_activity(
        self,
        user_id: int,
        activity_group_id: int,
        topic_id: int,
        topic_name: Optional[str] = None
    ):
        """Create or update a user's Activity GC topic mapping."""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO ai_activity (
                    user_id,
                    activity_group_id,
                    topic_id,
                    topic_name,
                    updated_at
                )
                VALUES ($1, $2, $3, $4, NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    activity_group_id = EXCLUDED.activity_group_id,
                    topic_id = EXCLUDED.topic_id,
                    topic_name = EXCLUDED.topic_name,
                    updated_at = NOW()
            """,
                user_id,
                activity_group_id,
                topic_id,
                topic_name
            )

     # ============================================================
    # PRIVATE AI TASK METHODS
    # ============================================================

    async def create_ai_task(
        self,
        user_id: int,
        task_text: str,
        schedule_type: str,
        schedule_data: Dict,
        timezone_name: str = "Asia/Kolkata",
        next_run_at: Optional[datetime] = None
    ) -> int:
        """Create a persistent AI reminder/task."""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            task_id = await conn.fetchval("""
                INSERT INTO ai_tasks (
                    user_id,
                    task_text,
                    schedule_type,
                    schedule_data,
                    timezone,
                    next_run_at
                )
                VALUES ($1, $2, $3, $4::jsonb, $5, $6)
                RETURNING id
            """,
                user_id,
                task_text,
                schedule_type,
                json.dumps(schedule_data),
                timezone_name,
                next_run_at
            )

            return int(task_id)

    async def get_active_ai_tasks(
        self,
        user_id: Optional[int] = None
    ) -> List[Dict]:
        """Get active AI tasks, optionally for one user."""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            if user_id is not None:
                rows = await conn.fetch("""
                    SELECT *
                    FROM ai_tasks
                    WHERE user_id = $1
                      AND active = TRUE
                    ORDER BY next_run_at NULLS LAST, id
                """, user_id)
            else:
                rows = await conn.fetch("""
                    SELECT *
                    FROM ai_tasks
                    WHERE active = TRUE
                    ORDER BY next_run_at NULLS LAST, id
                """)

            return [dict(row) for row in rows]

    async def deactivate_ai_task(
        self,
        task_id: int,
        user_id: int
    ) -> bool:
        """Deactivate one user's AI task."""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE ai_tasks
                SET
                    active = FALSE,
                    updated_at = NOW()
                WHERE id = $1
                  AND user_id = $2
                  AND active = TRUE
            """, task_id, user_id)

            return result.endswith("1")

    async def update_ai_task_next_run(
        self,
        task_id: int,
        next_run_at: Optional[datetime],
        active: bool = True
    ):
        """Update the next scheduled execution of a task."""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE ai_tasks
                SET
                    next_run_at = $2,
                    active = $3,
                    updated_at = NOW()
                WHERE id = $1
            """, task_id, next_run_at, active)

    async def get_active_ai_task(
        self,
        user_id: int,
        task_id: int
    ) -> Optional[Dict]:
        """Get one active task owned by a specific user."""

        if not self.pool:
            raise RuntimeError(
                "Database pool not initialized"
            )

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT *
                FROM ai_tasks
                WHERE id = $1
                  AND user_id = $2
                  AND active = TRUE
            """,
                task_id,
                user_id
            )

            return (
                dict(row)
                if row
                else None
            )

    async def update_ai_task(
        self,
        task_id: int,
        user_id: int,
        task_text: str,
        schedule_type: str,
        schedule_data: Dict,
        timezone_name: str,
        next_run_at: datetime
    ) -> bool:
        """
        Update an existing active task while preserving its ID.
        Ownership is enforced by user_id.
        """

        if not self.pool:
            raise RuntimeError(
                "Database pool not initialized"
            )

        async with self.pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE ai_tasks
                SET
                    task_text = $3,
                    schedule_type = $4,
                    schedule_data = $5::jsonb,
                    timezone = $6,
                    next_run_at = $7,
                    updated_at = NOW()
                WHERE id = $1
                  AND user_id = $2
                  AND active = TRUE
            """,
                task_id,
                user_id,
                task_text,
                schedule_type,
                json.dumps(schedule_data),
                timezone_name,
                next_run_at
            )

            return result.endswith("1")        

    async def get_due_ai_tasks(
        self,
        now_utc: datetime,
        limit: int = 100
    ) -> List[Dict]:
        """
        Get active reminders whose next execution time
        has arrived.
        
        ai_tasks.next_run_at is stored as naive UTC.
        """
        
        if not self.pool:
            raise RuntimeError(
                "Database pool not initialized"
            )
            
        async with self.pool.acquire() as conn:
            
            rows = await conn.fetch("""
                SELECT *
                FROM ai_tasks
                WHERE active = TRUE
                AND next_run_at IS NOT NULL
                AND next_run_at <= $1
                ORDER BY next_run_at ASC
                LIMIT $2
            """,
                now_utc,
                limit
            )
            
            return [
                dict(row)
                for row in rows
            ]   

    async def get_ai_analytics(
        self
    ) -> Dict:
        """Return aggregate operational statistics for Private AI."""

        if not self.pool:
            raise RuntimeError(
                "Database pool not initialized"
            )

        async with self.pool.acquire() as conn:

            row = await conn.fetchrow("""
                SELECT
                    (
                        SELECT COUNT(*)
                        FROM ai_profiles
                    ) AS ai_profiles,

                    (
                        SELECT COUNT(*)
                        FROM ai_credits
                    ) AS credit_users,

                    (
                        SELECT COALESCE(
                            SUM(balance),
                            0
                        )
                        FROM ai_credits
                    ) AS credits_available,

                    (
                        SELECT COALESCE(
                            SUM(total_earned),
                            0
                        )
                        FROM ai_credits
                    ) AS credits_earned,

                    (
                        SELECT COALESCE(
                            SUM(total_used),
                            0
                        )
                        FROM ai_credits
                    ) AS credits_used,

                    (
                        SELECT COUNT(*)
                        FROM ai_sessions
                        WHERE active = TRUE
                    ) AS active_sessions,

                    (
                        SELECT COUNT(*)
                        FROM ai_sessions
                    ) AS total_sessions,

                    (
                        SELECT COUNT(*)
                        FROM ai_tasks
                    ) AS total_tasks,

                    (
                        SELECT COUNT(*)
                        FROM ai_tasks
                        WHERE active = TRUE
                    ) AS active_tasks,

                    (
                        SELECT COUNT(*)
                        FROM ai_activity
                    ) AS activity_topics,

                    (
                        SELECT COUNT(*)
                        FROM ai_credit_transactions
                        WHERE created_at >= NOW() - INTERVAL '24 hours'
                    ) AS credit_transactions_24h,

                    (
                        SELECT COUNT(DISTINCT user_id)
                        FROM ai_sessions
                        WHERE started_at >= NOW() - INTERVAL '24 hours'
                    ) AS ai_users_24h
            """)

            return dict(row)    

    async def get_ai_user_snapshot(
        self,
        user_id: int
    ) -> Optional[Dict]:
        """
        Return operational AI information for one user.

        Private memory values are intentionally not returned.
        """

        if not self.pool:
            raise RuntimeError(
                "Database pool not initialized"
            )

        async with self.pool.acquire() as conn:

            row = await conn.fetchrow("""
                SELECT
                    u.id,
                    u.username,
                    u.first_name,
                    u.last_name,
                    u.created_at AS user_created_at,

                    COALESCE(
                        c.balance,
                        0
                    ) AS balance,

                    COALESCE(
                        c.total_earned,
                        0
                    ) AS total_earned,

                    COALESCE(
                        c.total_used,
                        0
                    ) AS total_used,

                    c.last_bonus_at,

                    EXISTS (
                        SELECT 1
                        FROM ai_profiles p
                        WHERE p.user_id = u.id
                    ) AS has_ai_profile,

                    EXISTS (
                        SELECT 1
                        FROM ai_sessions s
                        WHERE s.user_id = u.id
                          AND s.active = TRUE
                    ) AS active_session,

                    (
                        SELECT COUNT(*)
                        FROM ai_sessions s
                        WHERE s.user_id = u.id
                    ) AS total_sessions,

                    (
                        SELECT COUNT(*)
                        FROM ai_tasks t
                        WHERE t.user_id = u.id
                          AND t.active = TRUE
                    ) AS active_tasks,

                    (
                        SELECT COUNT(*)
                        FROM ai_tasks t
                        WHERE t.user_id = u.id
                    ) AS total_tasks,

                    EXISTS (
                        SELECT 1
                        FROM ai_activity a
                        WHERE a.user_id = u.id
                    ) AS has_activity_topic,

                    (
                        SELECT COUNT(*)
                        FROM ai_credit_transactions ct
                        WHERE ct.user_id = u.id
                    ) AS credit_transactions

                FROM users u

                LEFT JOIN ai_credits c
                    ON c.user_id = u.id

                WHERE u.id = $1
            """,
                user_id
            )

            return (
                dict(row)
                if row
                else None
            )    

    async def get_ai_memory_status(
        self,
        user_id: int
    ) -> Dict:
        """
        Return only which personalization fields are populated.
        Never return their private values.
        """

        if not self.pool:
            raise RuntimeError(
                "Database pool not initialized"
            )

        async with self.pool.acquire() as conn:

            row = await conn.fetchrow("""
                SELECT
                    preferred_name IS NOT NULL
                        AS has_name,

                    study_class IS NOT NULL
                        AS has_study_class,

                    exam_target IS NOT NULL
                        AS has_exam_target,

                    goals IS NOT NULL
                        AS has_goals,

                    preferences IS NOT NULL
                        AS has_preferences,

                    memory_summary IS NOT NULL
                        AS has_memory_summary

                FROM ai_profiles
                WHERE user_id = $1
            """,
                user_id
            )

            if not row:
                return {
                    "has_name": False,
                    "has_study_class": False,
                    "has_exam_target": False,
                    "has_goals": False,
                    "has_preferences": False,
                    "has_memory_summary": False,
                }

            return dict(row)    

    async def get_top_ai_users(
        self,
        limit: int = 10
    ) -> List[Dict]:
        """
        Rank users by actual recorded AI credits consumed.

        This is usage analytics, not a quality/performance ranking.
        """

        if not self.pool:
            raise RuntimeError(
                "Database pool not initialized"
            )

        limit = max(
            1,
            min(
                int(limit),
                25
            )
        )

        async with self.pool.acquire() as conn:

            rows = await conn.fetch("""
                SELECT
                    u.id,
                    u.username,
                    u.first_name,
                    c.balance,
                    c.total_earned,
                    c.total_used
                FROM ai_credits c

                JOIN users u
                    ON u.id = c.user_id

                WHERE c.total_used > 0

                ORDER BY
                    c.total_used DESC,
                    u.id ASC

                LIMIT $1
            """,
                limit
            )

            return [
                dict(row)
                for row in rows
            ]        
    
    async def add_group(self, group_id: int, title: str, group_type: str, username: Optional[str] = None, clone_bot_id: Optional[int] = None):
        """Add or update group in database"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO groups (id, title, type, username, clone_bot_id, updated_at)
                VALUES ($1, $2, $3, $4, $5, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    title = $2,
                    type = $3,
                    username = COALESCE(EXCLUDED.username, groups.username),
                    clone_bot_id = COALESCE(groups.clone_bot_id, EXCLUDED.clone_bot_id),
                    updated_at = NOW()
            """, group_id, title, group_type, username, clone_bot_id)
    
    async def add_group_member(self, user_id: int, group_id: int):
        """Add user to group"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO group_members (user_id, group_id)
                VALUES ($1, $2)
                ON CONFLICT (user_id, group_id) DO NOTHING
            """, user_id, group_id)
    
    async def is_admin(self, user_id: int) -> bool:
        """Check if user is admin"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            result = await conn.fetchval(
                "SELECT user_id FROM admins WHERE user_id = $1", user_id
            )
            return result is not None
    
    async def add_admin(self, user_id: int, username: Optional[str] = None, first_name: Optional[str] = None, promoted_by: Optional[int] = None):
        """Add admin to database"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO admins (user_id, username, first_name, promoted_by)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id) DO NOTHING
            """, user_id, username, first_name, promoted_by)
    
    async def remove_admin(self, user_id: int):
        """Remove admin from database"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM admins WHERE user_id = $1", user_id)
    
    async def get_all_admins(self) -> List[Dict]:
        """Get all admins"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM admins ORDER BY created_at")
            return [dict(row) for row in rows]
    
    async def add_quiz(self, message_id: int, from_group_id: int, quiz_text: str, correct_option: int, options: List[str]) -> int:
        """Add quiz to database and return quiz_id"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            quiz_id = await conn.fetchval("""
                INSERT INTO quizzes (message_id, from_group_id, quiz_text, correct_option, options)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                RETURNING id
            """, message_id, from_group_id, quiz_text, correct_option, json.dumps(options))
            return quiz_id
    
    async def update_quiz_correct_option(self, quiz_id: int, correct_option: int):
        """Update correct option for an existing quiz"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE quizzes SET correct_option = $2
                WHERE id = $1
            """, quiz_id, correct_option)
    
    async def record_quiz_answer(self, user_id: int, group_id: int, quiz_id: int, selected_option: int, points: int):
        """Record user's quiz answer"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO user_quiz_scores (user_id, group_id, quiz_id, selected_option, points)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (user_id, quiz_id, group_id) DO NOTHING
            """, user_id, group_id, quiz_id, selected_option, points)
            
            # Update user total score
            await conn.execute("""
                UPDATE users SET 
                    total_score = total_score + $2,
                    correct_answers = correct_answers + CASE WHEN $2 = 4 THEN 1 ELSE 0 END,
                    wrong_answers = wrong_answers + CASE WHEN $2 = -1 THEN 1 ELSE 0 END,
                    unattempted = unattempted + CASE WHEN $2 = 0 THEN 1 ELSE 0 END,
                    updated_at = NOW()
                WHERE id = $1
            """, user_id, points)
    
    async def get_group_leaderboard(self, group_id: int) -> List[Dict]:
        """Get leaderboard for specific group"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT u.id, u.username, u.first_name, u.last_name,
                       COALESCE(SUM(uqs.points), 0) as score,
                       COUNT(CASE WHEN uqs.points = 4 THEN 1 END) as correct,
                       COUNT(CASE WHEN uqs.points = -1 THEN 1 END) as wrong,
                       COUNT(CASE WHEN uqs.points = 0 THEN 1 END) as unattempted
                FROM users u
                JOIN group_members gm ON u.id = gm.user_id
                LEFT JOIN user_quiz_scores uqs ON u.id = uqs.user_id AND uqs.group_id = $1
                WHERE gm.group_id = $1
                  AND (uqs.quiz_id IS NOT NULL OR 
                       EXISTS(SELECT 1 FROM user_quiz_scores WHERE user_id = u.id AND group_id = $1))
                GROUP BY u.id, u.username, u.first_name, u.last_name
                ORDER BY score DESC, correct DESC
            """, group_id)
            return [dict(row) for row in rows]
    
    async def get_universal_leaderboard(self, limit: int = 50) -> List[Dict]:
        """Get universal leaderboard across all groups"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT u.id, u.username, u.first_name, u.last_name, u.total_score as score
                FROM users u
                WHERE u.total_score > 0
                ORDER BY u.total_score DESC, u.correct_answers DESC
                LIMIT $1
            """, limit)
            return [dict(row) for row in rows]
    
    async def get_daily_universal_leaderboard(self, limit: int = 50) -> List[Dict]:
        """Get universal leaderboard based on last 24 hours scores only"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT u.id, u.username, u.first_name, u.last_name,
                       COALESCE(SUM(uqs.points), 0) as score,
                       COUNT(CASE WHEN uqs.points = 4 THEN 1 END) as correct,
                       COUNT(CASE WHEN uqs.points = -1 THEN 1 END) as wrong
                FROM users u
                JOIN user_quiz_scores uqs ON u.id = uqs.user_id
                WHERE uqs.answered_at >= NOW() - INTERVAL '24 hours'
                GROUP BY u.id, u.username, u.first_name, u.last_name
                HAVING COALESCE(SUM(uqs.points), 0) > 0
                ORDER BY score DESC, correct DESC
                LIMIT $1
            """, limit)
            return [dict(row) for row in rows]
    
    async def get_top_users_by_activity(self, limit: int = 10) -> List[Dict]:
        """Get top N users ranked by total quiz answers submitted"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT u.id, u.username, u.first_name, u.last_name,
                       COUNT(uqs.id) AS answer_count
                FROM users u
                JOIN user_quiz_scores uqs ON u.id = uqs.user_id
                GROUP BY u.id, u.username, u.first_name, u.last_name
                ORDER BY answer_count DESC
                LIMIT $1
            """, limit)
            return [dict(row) for row in rows]

    async def get_top_groups_by_activity(self, limit: int = 10) -> List[Dict]:
        """Get top N groups ranked by total quiz answers submitted in them"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT g.id, g.title, g.username,
                       COUNT(uqs.id) AS answer_count
                FROM groups g
                JOIN user_quiz_scores uqs ON g.id = uqs.group_id
                WHERE g.is_active = TRUE
                GROUP BY g.id, g.title, g.username
                ORDER BY answer_count DESC
                LIMIT $1
            """, limit)
            return [dict(row) for row in rows]

    async def get_top_groups_by_members(self, limit: int = 10) -> List[Dict]:
        """Get top N active groups ranked by registered member count"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT g.id, g.title, g.username,
                       COUNT(gm.user_id) AS member_count
                FROM groups g
                JOIN group_members gm ON g.id = gm.group_id
                WHERE g.is_active = TRUE
                GROUP BY g.id, g.title, g.username
                ORDER BY member_count DESC
                LIMIT $1
            """, limit)
            return [dict(row) for row in rows]

    async def get_all_groups(self) -> List[Dict]:
        """Get all active groups"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM groups WHERE is_active = TRUE")
            return [dict(row) for row in rows]
    
    async def get_all_users(self) -> List[Dict]:
        """Get all users who have interacted with the bot"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, username, first_name, last_name FROM users")
            return [dict(row) for row in rows]
    
    async def get_bot_stats(self) -> Dict:
        """Get bot statistics"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
            total_groups = await conn.fetchval("SELECT COUNT(*) FROM groups WHERE is_active = TRUE")
            total_quizzes = await conn.fetchval("SELECT COUNT(*) FROM quizzes")
            total_answers = await conn.fetchval("SELECT COUNT(*) FROM user_quiz_scores")
            
            return {
                "total_users": total_users,
                "total_groups": total_groups,
                "total_quizzes": total_quizzes,
                "total_answers": total_answers
            }
    
    async def fetchval(self, query: str, *args):
        """Execute query and return single value"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            return await conn.fetchval(query, *args)
    
    async def execute(self, query: str, *args):
        """Execute query without return value"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)
    
    async def reset_weekly_leaderboard(self):
        """Reset all user scores and quiz scores for weekly leaderboard reset"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            # Reset all user total scores and stats
            await conn.execute("""
                UPDATE users SET 
                    total_score = 0,
                    correct_answers = 0,
                    wrong_answers = 0,
                    unattempted = 0,
                    updated_at = NOW()
            """)
            
            # Delete all user quiz scores
            await conn.execute("DELETE FROM user_quiz_scores")
            
            logger.info("Weekly leaderboard reset completed successfully")
    
    async def set_quiz_solution(self, quiz_id: int, solution_type: str, solution_content: str):
        """Set solution for a quiz"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO quiz_solutions (quiz_id, solution_type, solution_content, updated_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (quiz_id) DO UPDATE SET
                    solution_type = $2,
                    solution_content = $3,
                    updated_at = NOW()
            """, quiz_id, solution_type, solution_content)
    
    async def get_quiz_solution(self, quiz_id: int) -> Optional[Dict]:
        """Get solution for a quiz"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT solution_type, solution_content, updated_at
                FROM quiz_solutions
                WHERE quiz_id = $1
            """, quiz_id)
            return dict(row) if row else None
    
    async def get_quiz_by_message_id(self, message_id: int, group_id: int) -> Optional[Dict]:
        """Get quiz by message ID from specific group"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT id, quiz_text, correct_option, options
                FROM quizzes
                WHERE message_id = $1 AND from_group_id = $2
            """, message_id, group_id)
            return dict(row) if row else None
    
    async def add_custom_reply(self, reply_type: str, message_type: str, content: Optional[str] = None, 
                              file_id: Optional[str] = None, caption: Optional[str] = None, added_by: Optional[int] = None) -> int:
        """Add custom reply to database"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            reply_id = await conn.fetchval("""
                INSERT INTO custom_replies (reply_type, message_type, content, file_id, caption, added_by)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
            """, reply_type, message_type, content, file_id, caption, added_by)
            return reply_id
    
    async def get_custom_replies(self, reply_type: str) -> List[Dict]:
        """Get all custom replies of a specific type (positive/negative)"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, message_type, content, file_id, caption
                FROM custom_replies
                WHERE reply_type = $1
                ORDER BY created_at DESC
            """, reply_type)
            return [dict(row) for row in rows]
    
    async def remove_custom_reply(self, content: Optional[str] = None, file_id: Optional[str] = None) -> int:
        """Remove custom reply by content or file_id. Returns number of deleted rows"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            if content:
                result = await conn.execute("""
                    DELETE FROM custom_replies WHERE content = $1
                """, content)
            elif file_id:
                result = await conn.execute("""
                    DELETE FROM custom_replies WHERE file_id = $1
                """, file_id)
            else:
                return 0
            
            # Extract number of deleted rows from result string
            deleted_count = int(result.split()[-1]) if result else 0
            return deleted_count
    
    async def set_group_replies_status(self, group_id: int, enabled: bool):
        """Enable or disable replies for a group"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE groups SET replies_enabled = $2, updated_at = NOW()
                WHERE id = $1
            """, group_id, enabled)
    
    async def is_group_replies_enabled(self, group_id: int) -> bool:
        """Check if replies are enabled for a group"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            result = await conn.fetchval("""
                SELECT replies_enabled FROM groups WHERE id = $1
            """, group_id)
            return result if result is not None else True
    
    async def store_message_mapping(self, forwarded_message_id: int, user_id: int):
        """Store mapping of forwarded message to user"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO message_mapping (forwarded_message_id, user_id)
                VALUES ($1, $2)
                ON CONFLICT (forwarded_message_id) DO UPDATE SET user_id = $2
            """, forwarded_message_id, user_id)
    
    async def get_user_from_message(self, forwarded_message_id: int) -> Optional[int]:
        """Get user_id from forwarded message ID"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            result = await conn.fetchval("""
                SELECT user_id FROM message_mapping WHERE forwarded_message_id = $1
            """, forwarded_message_id)
            return result
    
    async def get_user_universal_rank(self, user_id: int) -> Optional[int]:
        """Get user's universal rank across all groups"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            # Get rank by counting users with higher total_score
            rank = await conn.fetchval("""
                SELECT COUNT(*) + 1 
                FROM users 
                WHERE total_score > (SELECT total_score FROM users WHERE id = $1)
            """, user_id)
            return rank
    
    async def get_user_group_scores(self, user_id: int) -> List[Dict]:
        """Get user's scores and ranks for all groups they participated in"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            # Get user's group-wise scores
            rows = await conn.fetch("""
                WITH user_group_scores AS (
                    SELECT 
                        g.id as group_id,
                        g.title as group_name,
                        COALESCE(SUM(uqs.points), 0) as score,
                        COUNT(CASE WHEN uqs.points = 4 THEN 1 END) as correct,
                        COUNT(CASE WHEN uqs.points = -1 THEN 1 END) as wrong,
                        COUNT(CASE WHEN uqs.points = 0 THEN 1 END) as unattempted
                    FROM groups g
                    JOIN user_quiz_scores uqs ON g.id = uqs.group_id
                    WHERE uqs.user_id = $1
                    GROUP BY g.id, g.title
                ),
                group_ranks AS (
                    SELECT 
                        uqs.group_id,
                        uqs.user_id,
                        RANK() OVER (PARTITION BY uqs.group_id ORDER BY SUM(uqs.points) DESC) as rank
                    FROM user_quiz_scores uqs
                    GROUP BY uqs.group_id, uqs.user_id
                )
                SELECT 
                    ugs.group_id,
                    ugs.group_name,
                    ugs.score,
                    ugs.correct,
                    ugs.wrong,
                    ugs.unattempted,
                    COALESCE(gr.rank, 0) as rank
                FROM user_group_scores ugs
                LEFT JOIN group_ranks gr ON ugs.group_id = gr.group_id AND gr.user_id = $1
                ORDER BY ugs.score DESC
            """, user_id)
            return [dict(row) for row in rows]
    
    async def set_group_language(self, group_id: int, language: str):
        """Set language preference for a group"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE groups SET language_preference = $2, updated_at = NOW()
                WHERE id = $1
            """, group_id, language.lower())
        # Invalidate cache
        self._invalidate_cache(f'group_language_{group_id}')
    
    async def get_group_language(self, group_id: int) -> str:
        """Get language preference for a group (cached to reduce DB queries)"""
        cache_key = f'group_language_{group_id}'
        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached
        
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            result = await conn.fetchval("""
                SELECT language_preference FROM groups WHERE id = $1
            """, group_id)
            language = result if result is not None else 'english'
            self._set_cache(cache_key, language, self._cache_ttl['group_language'])
            return language

    async def set_user_language(self, user_id: int, language: str):
        """Set language preference for a user (private chat)"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE users SET language_preference = $2, updated_at = NOW()
                WHERE id = $1
            """, user_id, language.lower())
        self._invalidate_cache(f'user_language_{user_id}')

    async def get_user_language(self, user_id: int) -> str:
        """Get language preference for a user (private chat), cached"""
        cache_key = f'user_language_{user_id}'
        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached

        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            result = await conn.fetchval("""
                SELECT language_preference FROM users WHERE id = $1
            """, user_id)
            language = result if result is not None else 'english'
            self._set_cache(cache_key, language, self._cache_ttl['group_language'])
            return language
    
    async def add_clone_bot(self, bot_token: str, bot_id: int, bot_name: str, bot_username: str,
                            owner_id: int, owner_username: Optional[str], owner_name: Optional[str]) -> bool:
        """Register a new clone bot. Returns True if added, False if already exists."""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            existing = await conn.fetchval("SELECT bot_id FROM clone_bots WHERE bot_id = $1 OR owner_id = $2", bot_id, owner_id)
            if existing:
                return False
            await conn.execute("""
                INSERT INTO clone_bots (bot_token, bot_id, bot_name, bot_username, owner_id, owner_username, owner_name)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
            """, bot_token, bot_id, bot_name, bot_username, owner_id, owner_username, owner_name)
            return True

    async def set_clone_pending(self, user_id: int):
        """Mark user as pending clone setup"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO clone_pending (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
                user_id
            )

    async def is_clone_pending(self, user_id: int) -> bool:
        """Check if user is in clone setup pending state"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            row = await conn.fetchval("SELECT user_id FROM clone_pending WHERE user_id = $1", user_id)
            return row is not None

    async def clear_clone_pending(self, user_id: int):
        """Remove user from clone pending state"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM clone_pending WHERE user_id = $1", user_id)

    async def get_all_active_clone_bots(self) -> List[Dict]:
        """Get all active (not paused) clone bots"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM clone_bots WHERE is_active = TRUE AND is_paused = FALSE ORDER BY created_at
            """)
            return [dict(r) for r in rows]

    async def get_all_clone_bots(self) -> List[Dict]:
        """Get all clone bots including paused ones"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM clone_bots ORDER BY created_at")
            return [dict(r) for r in rows]

    async def get_clone_bot(self, bot_id: int) -> Optional[Dict]:
        """Get clone bot by its Telegram bot_id"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM clone_bots WHERE bot_id = $1", bot_id)
            return dict(row) if row else None

    async def get_clone_bot_by_owner(self, owner_id: int) -> Optional[Dict]:
        """Get clone bot by owner's user_id"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM clone_bots WHERE owner_id = $1", owner_id)
            return dict(row) if row else None

    async def pause_clone_bot(self, bot_id: int, reason: str):
        """Pause a clone bot"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE clone_bots SET is_paused = TRUE, pause_reason = $2 WHERE bot_id = $1
            """, bot_id, reason)

    async def resume_clone_bot(self, bot_id: int):
        """Resume a paused clone bot"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE clone_bots SET is_paused = FALSE, pause_reason = NULL WHERE bot_id = $1
            """, bot_id)

    async def get_clone_groups(self, clone_bot_id: int) -> List[Dict]:
        """Get all groups registered under a clone bot"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM groups WHERE clone_bot_id = $1 AND is_active = TRUE
            """, clone_bot_id)
            return [dict(r) for r in rows]

    async def get_clone_users(self, clone_bot_id: int) -> List[Dict]:
        """Get all users registered under a clone bot"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM users WHERE clone_bot_id = $1
            """, clone_bot_id)
            return [dict(r) for r in rows]

    async def get_clone_bot_stats(self, clone_bot_id: int) -> Dict:
        """Get stats for a clone bot"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            users_count = await conn.fetchval("SELECT COUNT(*) FROM users WHERE clone_bot_id = $1", clone_bot_id)
            groups_count = await conn.fetchval(
                "SELECT COUNT(*) FROM groups WHERE clone_bot_id = $1 AND is_active = TRUE AND type IN ('group','supergroup')", clone_bot_id)
            channels_count = await conn.fetchval(
                "SELECT COUNT(*) FROM groups WHERE clone_bot_id = $1 AND is_active = TRUE AND type = 'channel'", clone_bot_id)
            answers_count = await conn.fetchval("""
                SELECT COUNT(*) FROM user_quiz_scores uqs
                JOIN groups g ON uqs.group_id = g.id
                WHERE g.clone_bot_id = $1
            """, clone_bot_id)
            return {
                'users': users_count or 0,
                'groups': groups_count or 0,
                'channels': channels_count or 0,
                'total_answers': answers_count or 0
            }

    async def get_clone_leaderboard(self, clone_bot_id: int, limit: int = 10) -> List[Dict]:
        """Get leaderboard for a clone bot's audience"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT u.id, u.first_name, u.username, SUM(uqs.points) as total_score
                FROM user_quiz_scores uqs
                JOIN users u ON uqs.user_id = u.id
                JOIN groups g ON uqs.group_id = g.id
                WHERE g.clone_bot_id = $1
                GROUP BY u.id, u.first_name, u.username
                ORDER BY total_score DESC
                LIMIT $2
            """, clone_bot_id, limit)
            return [dict(r) for r in rows]

    async def add_poll_mapping(self, poll_id: str, quiz_id: int, group_id: int,
                                message_id: int, clone_bot_id: int, correct_option: int):
        """Store poll ID → quiz mapping for clone bots"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO poll_mappings (poll_id, quiz_id, group_id, message_id, clone_bot_id, correct_option)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (poll_id) DO NOTHING
            """, poll_id, quiz_id, group_id, message_id, clone_bot_id, correct_option)

    async def get_poll_mapping(self, poll_id: str) -> Optional[Dict]:
        """Get poll mapping by poll_id"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM poll_mappings WHERE poll_id = $1", poll_id)
            return dict(row) if row else None

    async def add_force_join_group(self, chat_id: int, chat_username: Optional[str] = None, 
                                   chat_title: Optional[str] = None, chat_type: Optional[str] = None,
                                   invite_link: Optional[str] = None, added_by: Optional[int] = None) -> bool:
        """Add a group/channel to force join list. Returns True if added, False if limit reached for new groups"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            # Check if this chat_id already exists
            existing = await conn.fetchval("SELECT chat_id FROM force_join_groups WHERE chat_id = $1", chat_id)
            
            # If it doesn't exist, check if we've reached the limit
            if not existing:
                count = await conn.fetchval("SELECT COUNT(*) FROM force_join_groups")
                if count >= 5:
                    return False
            
            # Add or update the group
            await conn.execute("""
                INSERT INTO force_join_groups (chat_id, chat_username, chat_title, chat_type, invite_link, added_by)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (chat_id) DO UPDATE SET
                    chat_username = $2,
                    chat_title = $3,
                    chat_type = $4,
                    invite_link = $5
            """, chat_id, chat_username, chat_title, chat_type, invite_link, added_by)
            self._invalidate_cache('force_join_groups')  # Invalidate cache
            return True
    
    async def remove_force_join_group(self, chat_id: int) -> bool:
        """Remove a group/channel from force join list. Returns True if removed, False if not found"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            result = await conn.execute("DELETE FROM force_join_groups WHERE chat_id = $1", chat_id)
            deleted_count = int(result.split()[-1]) if result else 0
            if deleted_count > 0:
                self._invalidate_cache('force_join_groups')  # Invalidate cache
            return deleted_count > 0
    
    async def get_force_join_groups(self) -> List[Dict]:
        """Get all force join groups/channels (cached to reduce DB queries)"""
        cache_key = 'force_join_groups'
        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached
        
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM force_join_groups ORDER BY created_at")
            groups = [dict(row) for row in rows]
            self._set_cache(cache_key, groups, self._cache_ttl['force_join_groups'])
            return groups
    
    async def get_force_join_count(self) -> int:
        """Get count of force join groups"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            return await conn.fetchval("SELECT COUNT(*) FROM force_join_groups")
    
    async def get_user_daily_wrong_answers(self, user_id: int, date: datetime) -> List[Dict]:
        """Get all unique wrong answers for a user on a specific date (Indian timezone)"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        
        # Calculate start and end of the day in UTC (from IST)
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        
        # Create start and end of day in IST
        day_start_ist = ist.localize(datetime(date.year, date.month, date.day, 0, 0, 0))
        day_end_ist = ist.localize(datetime(date.year, date.month, date.day, 23, 59, 59))
        
        # Convert to naive UTC for asyncpg compatibility
        day_start_utc = day_start_ist.astimezone(pytz.UTC).replace(tzinfo=None)
        day_end_utc = day_end_ist.astimezone(pytz.UTC).replace(tzinfo=None)
        
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT DISTINCT ON (q.id) 
                    q.id as quiz_id,
                    q.quiz_text,
                    q.options,
                    q.correct_option,
                    uqs.selected_option,
                    uqs.answered_at
                FROM user_quiz_scores uqs
                JOIN quizzes q ON uqs.quiz_id = q.id
                WHERE uqs.user_id = $1 
                  AND uqs.points = -1
                  AND uqs.answered_at >= $2
                  AND uqs.answered_at <= $3
                ORDER BY q.id, uqs.answered_at DESC
            """, user_id, day_start_utc, day_end_utc)
            return [dict(row) for row in rows]
    
    async def get_users_with_wrong_answers_today(self, date: datetime) -> List[int]:
        """Get list of user IDs who have wrong answers today"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        
        # Create start and end of day in IST
        day_start_ist = ist.localize(datetime(date.year, date.month, date.day, 0, 0, 0))
        day_end_ist = ist.localize(datetime(date.year, date.month, date.day, 23, 59, 59))
        
        # Convert to naive UTC for asyncpg compatibility
        day_start_utc = day_start_ist.astimezone(pytz.UTC).replace(tzinfo=None)
        day_end_utc = day_end_ist.astimezone(pytz.UTC).replace(tzinfo=None)
        
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT DISTINCT user_id
                FROM user_quiz_scores
                WHERE points = -1
                  AND answered_at >= $1
                  AND answered_at <= $2
            """, day_start_utc, day_end_utc)
            return [row['user_id'] for row in rows]
    
    async def store_sent_message(self, original_message_id: int, original_chat_id: int, 
                                  sent_message_id: int, sent_chat_id: int, sent_by: int):
        """Store a sent message mapping for later deletion"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO sent_messages (original_message_id, original_chat_id, sent_message_id, sent_chat_id, sent_by)
                VALUES ($1, $2, $3, $4, $5)
            """, original_message_id, original_chat_id, sent_message_id, sent_chat_id, sent_by)
    
    async def get_sent_messages(self, original_message_id: int, original_chat_id: int) -> List[Dict]:
        """Get all sent messages for a given original message"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT sent_message_id, sent_chat_id
                FROM sent_messages
                WHERE original_message_id = $1 AND original_chat_id = $2
            """, original_message_id, original_chat_id)
            return [dict(row) for row in rows]
    
    async def delete_sent_message_records(self, original_message_id: int, original_chat_id: int):
        """Delete all sent message records for a given original message"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                DELETE FROM sent_messages
                WHERE original_message_id = $1 AND original_chat_id = $2
            """, original_message_id, original_chat_id)

    async def save_auto_quiz(
        self,
        subject: str,
        source_chat_id: int,
        source_message_id: int,
        source_poll_id: str,
        question: str,
        options: list,
        correct_option=None
    ):
        """
        Save/update a quiz captured from an automatic source channel.

        READY   = a valid correct answer is known.
        PENDING = correct answer is not known yet.

        Existing READY answers are preserved if a later Bot API update
        arrives without correct_option.
        """
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        subject = str(subject or "").strip().lower()
        if subject not in {"biology", "chemistry", "physics"}:
            raise ValueError(f"Invalid auto quiz subject: {subject}")

        question = str(question or "").strip()
        if not question:
            raise ValueError("Auto quiz question cannot be empty")

        clean_options = [
            str(option).strip()
            for option in (options or [])
            if str(option).strip()
        ]

        if len(clean_options) < 2:
            raise ValueError("Auto quiz must contain at least 2 options")

        valid_correct_option = None

        if correct_option is not None:
            try:
                candidate = int(correct_option)

                if 0 <= candidate < len(clean_options):
                    valid_correct_option = candidate

            except (TypeError, ValueError):
                valid_correct_option = None

        status = (
            "ready"
            if valid_correct_option is not None
            else "pending"
        )

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO auto_quiz_bank (
                    subject,
                    source_chat_id,
                    source_message_id,
                    source_poll_id,
                    question,
                    options,
                    correct_option,
                    status,
                    updated_at
                )
                VALUES (
                    $1,
                    $2,
                    $3,
                    $4,
                    $5,
                    $6::jsonb,
                    $7,
                    $8,
                    NOW()
                )

                ON CONFLICT (source_chat_id, source_message_id)
                DO UPDATE SET
                    subject = EXCLUDED.subject,
                    source_poll_id = EXCLUDED.source_poll_id,
                    question = EXCLUDED.question,
                    options = EXCLUDED.options,

                    correct_option = COALESCE(
                        EXCLUDED.correct_option,
                        auto_quiz_bank.correct_option
                    ),

                    status = CASE
                        WHEN auto_quiz_bank.status = 'disabled'
                        THEN 'disabled'

                        WHEN EXCLUDED.correct_option IS NOT NULL
                             OR auto_quiz_bank.correct_option IS NOT NULL
                        THEN 'ready'

                        ELSE 'pending'
                    END,

                    updated_at = NOW()

                RETURNING
                    id,
                    subject,
                    status,
                    correct_option
            """,
                subject,
                int(source_chat_id),
                int(source_message_id),
                (
                    str(source_poll_id)
                    if source_poll_id
                    else None
                ),
                question,
                json.dumps(clean_options),
                valid_correct_option,
                status
            )

            return dict(row)


    async def mark_auto_quiz_ready(
        self,
        source_chat_id: int,
        source_message_id: int,
        correct_option: int
    ):
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE auto_quiz_bank
                SET
                    correct_option = $3,
                    status = 'ready',
                    updated_at = NOW()
                WHERE source_chat_id = $1
                  AND source_message_id = $2
                  AND $3 >= 0
                  AND $3 < jsonb_array_length(options)
                  AND status != 'disabled'
                RETURNING
                    id,
                    subject,
                    correct_option,
                    status
            """,
                int(source_chat_id),
                int(source_message_id),
                int(correct_option)
            )

            return dict(row) if row else None


    async def is_auto_quiz_scheduler_enabled(self) -> bool:
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            value = await conn.fetchval("""
                SELECT enabled
                FROM auto_quiz_scheduler_state
                WHERE id = 1
            """)

            return bool(value) if value is not None else True


    async def claim_auto_quiz_slot(
        self,
        slot_key: str,
        subject: str
    ) -> bool:
        """
        Atomically claim a schedule slot.

        SENT slots are never reclaimed.
        FAILED/SKIPPED slots can retry.
        Stale CLAIMED slots recover after restart.
        """

        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        subject = str(subject or "").strip().lower()

        if subject not in {
            "biology",
            "chemistry",
            "physics"
        }:
            raise ValueError(
                f"Invalid auto quiz subject: {subject}"
            )

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO auto_quiz_runs (
                    slot_key,
                    subject,
                    status
                )
                VALUES ($1, $2, 'claimed')

                ON CONFLICT (slot_key) DO UPDATE SET
                    subject = EXCLUDED.subject,
                    status = 'claimed',
                    error_text = NULL,
                    completed_at = NULL,
                    claimed_at = NOW(),
                    updated_at = NOW()

                WHERE
                    (
                        auto_quiz_runs.status IN (
                            'failed',
                            'skipped'
                        )
                        AND auto_quiz_runs.updated_at
                            < NOW() - INTERVAL '30 seconds'
                    )
                    OR
                    (
                        auto_quiz_runs.status = 'claimed'
                        AND auto_quiz_runs.updated_at
                            < NOW() - INTERVAL '2 minutes'
                    )

                RETURNING slot_key
            """,
                slot_key,
                subject
            )

            return row is not None


    async def select_auto_quiz(
        self,
        subject: str
    ):
        """
        Select a random READY quiz without repeating it
        inside the subject's current cycle.

        Selection does not mark it as sent.
        """

        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        subject = str(subject or "").strip().lower()

        if subject not in {
            "biology",
            "chemistry",
            "physics"
        }:
            raise ValueError(
                f"Invalid auto quiz subject: {subject}"
            )

        async with self.pool.acquire() as conn:
            async with conn.transaction():

                state = await conn.fetchrow("""
                    SELECT current_cycle
                    FROM auto_quiz_subject_state
                    WHERE subject = $1
                    FOR UPDATE
                """, subject)

                if not state:
                    state = await conn.fetchrow("""
                        INSERT INTO auto_quiz_subject_state (
                            subject,
                            current_cycle
                        )
                        VALUES ($1, 1)

                        ON CONFLICT (subject)
                        DO UPDATE SET
                            subject = EXCLUDED.subject

                        RETURNING current_cycle
                    """, subject)

                current_cycle = int(
                    state["current_cycle"]
                )

                row = await conn.fetchrow("""
                    SELECT *
                    FROM auto_quiz_bank
                    WHERE subject = $1
                      AND status = 'ready'
                      AND correct_option IS NOT NULL
                      AND last_sent_cycle < $2
                    ORDER BY RANDOM()
                    LIMIT 1
                """,
                    subject,
                    current_cycle
                )

                if not row:
                    ready_count = await conn.fetchval("""
                        SELECT COUNT(*)
                        FROM auto_quiz_bank
                        WHERE subject = $1
                          AND status = 'ready'
                          AND correct_option IS NOT NULL
                    """, subject)

                    if not ready_count:
                        return None

                    current_cycle += 1

                    await conn.execute("""
                        UPDATE auto_quiz_subject_state
                        SET
                            current_cycle = $2,
                            updated_at = NOW()
                        WHERE subject = $1
                    """,
                        subject,
                        current_cycle
                    )

                    row = await conn.fetchrow("""
                        SELECT *
                        FROM auto_quiz_bank
                        WHERE subject = $1
                          AND status = 'ready'
                          AND correct_option IS NOT NULL
                        ORDER BY RANDOM()
                        LIMIT 1
                    """, subject)

                if not row:
                    return None

                result = dict(row)
                result["selected_cycle"] = current_cycle

                return result


    async def mark_auto_quiz_sent(
        self,
        quiz_id: int,
        cycle: int
    ):
        """
        Mark quiz used only after at least one
        destination successfully received it.
        """

        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE auto_quiz_bank
                SET
                    last_sent_cycle = GREATEST(
                        last_sent_cycle,
                        $2
                    ),
                    cycle_number = GREATEST(
                        cycle_number,
                        $2
                    ),
                    total_times_sent =
                        total_times_sent + 1,
                    last_sent_at = NOW(),
                    updated_at = NOW()
                WHERE id = $1
            """,
                int(quiz_id),
                int(cycle)
            )


    async def get_or_create_auto_quiz_scoring_id(
        self,
        auto_quiz_id: int
    ) -> int:
        """
        Give an automatic quiz a real quizzes.id so
        existing scoring and leaderboards keep working.
        """

        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            async with conn.transaction():

                row = await conn.fetchrow("""
                    SELECT *
                    FROM auto_quiz_bank
                    WHERE id = $1
                    FOR UPDATE
                """, int(auto_quiz_id))

                if not row:
                    raise ValueError(
                        "Automatic quiz not found"
                    )

                if row["scoring_quiz_id"] is not None:
                    return int(
                        row["scoring_quiz_id"]
                    )

                raw_options = row["options"]

                if isinstance(raw_options, str):
                    raw_options = json.loads(
                        raw_options
                    )

                scoring_id = await conn.fetchval("""
                    INSERT INTO quizzes (
                        message_id,
                        from_group_id,
                        quiz_text,
                        correct_option,
                        options
                    )
                    VALUES (
                        $1,
                        $2,
                        $3,
                        $4,
                        $5::jsonb
                    )
                    RETURNING id
                """,
                    int(row["source_message_id"]),
                    int(row["source_chat_id"]),
                    str(row["question"]),
                    int(row["correct_option"]),
                    json.dumps(
                        list(raw_options)
                    )
                )

                await conn.execute("""
                    UPDATE auto_quiz_bank
                    SET
                        scoring_quiz_id = $2,
                        updated_at = NOW()
                    WHERE id = $1
                """,
                    int(auto_quiz_id),
                    int(scoring_id)
                )

                return int(scoring_id)


    async def attach_auto_quiz_to_slot(
        self,
        slot_key: str,
        quiz_id: int
    ):
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE auto_quiz_runs
                SET
                    quiz_id = $2,
                    updated_at = NOW()
                WHERE slot_key = $1
            """,
                slot_key,
                int(quiz_id)
            )


    async def finish_auto_quiz_slot(
        self,
        slot_key: str,
        status: str,
        error_text=None
    ):
        if status not in {
            "sent",
            "failed",
            "skipped"
        }:
            raise ValueError(
                "Invalid auto quiz slot status"
            )

        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE auto_quiz_runs
                SET
                    status = $2,
                    error_text = $3,
                    completed_at = NOW(),
                    updated_at = NOW()
                WHERE slot_key = $1
            """,
                slot_key,
                status,
                (
                    str(error_text)[:1000]
                    if error_text
                    else None
                )
            )

    # ================================================================
    # AIRA — OWNER PERSONAL ASSISTANT
    # ================================================================

    async def get_aira_mode(self) -> str:
        """
        Return persistent AIRA mode.

        Possible values:
        - online
        - offline
        """

        if not self.pool:
            raise RuntimeError(
                "Database pool not initialized"
            )

        async with self.pool.acquire() as conn:
            mode = await conn.fetchval("""
                SELECT mode
                FROM aira_settings
                WHERE id = 1
            """)

            return (
                str(mode).lower()
                if mode
                else "online"
            )


    async def set_aira_mode(
        self,
        mode: str,
        updated_by: int
    ) -> str:
        """
        Persist AIRA online/offline mode.
        """

        if not self.pool:
            raise RuntimeError(
                "Database pool not initialized"
            )

        clean_mode = str(
            mode or ""
        ).strip().lower()

        if clean_mode not in {
            "online",
            "offline"
        }:
            raise ValueError(
                "AIRA mode must be online or offline"
            )

        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO aira_settings (
                    id,
                    mode,
                    updated_by,
                    updated_at
                )
                VALUES (
                    1,
                    $1,
                    $2,
                    NOW()
                )

                ON CONFLICT (id)
                DO UPDATE SET
                    mode = EXCLUDED.mode,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW()
            """,
                clean_mode,
                int(updated_by)
            )

        return clean_mode


    async def get_aira_user(
        self,
        user_id: int
    ) -> Optional[Dict]:
        """
        Get one personal-DM user's persistent AIRA state.
        """

        if not self.pool:
            raise RuntimeError(
                "Database pool not initialized"
            )

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT *
                FROM aira_users
                WHERE user_id = $1
            """,
                int(user_id)
            )

            return dict(row) if row else None


    async def upsert_aira_user(
        self,
        user_id: int,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        username: Optional[str] = None
    ) -> Dict:
        """
        Create/update basic Telegram identity.

        Existing activity timestamps and topic mapping
        are preserved.
        """

        if not self.pool:
            raise RuntimeError(
                "Database pool not initialized"
            )

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO aira_users (
                    user_id,
                    first_name,
                    last_name,
                    username,
                    updated_at
                )
                VALUES (
                    $1,
                    $2,
                    $3,
                    $4,
                    NOW()
                )

                ON CONFLICT (user_id)
                DO UPDATE SET
                    first_name = EXCLUDED.first_name,
                    last_name = EXCLUDED.last_name,
                    username = EXCLUDED.username,
                    updated_at = NOW()

                RETURNING *
            """,
                int(user_id),
                first_name,
                last_name,
                username
            )

            return dict(row)


    async def update_aira_user_activity(
        self,
        user_id: int,
        *,
        incoming: bool = False,
        owner_message: bool = False,
        auto_reply: bool = False,
        urgent_reply: bool = False
    ):
        """
        Update only requested AIRA timestamps.
        """

        if not self.pool:
            raise RuntimeError(
                "Database pool not initialized"
            )

        if not any((
            incoming,
            owner_message,
            auto_reply,
            urgent_reply
        )):
            return

        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO aira_users (
                    user_id,
                    last_incoming_at,
                    last_owner_message_at,
                    last_auto_reply_at,
                    last_urgent_reply_at,
                    updated_at
                )
                VALUES (
                    $1,
                    CASE WHEN $2 THEN NOW() ELSE NULL END,
                    CASE WHEN $3 THEN NOW() ELSE NULL END,
                    CASE WHEN $4 THEN NOW() ELSE NULL END,
                    CASE WHEN $5 THEN NOW() ELSE NULL END,
                    NOW()
                )

                ON CONFLICT (user_id)
                DO UPDATE SET

                    last_incoming_at =
                        CASE
                            WHEN $2
                            THEN NOW()
                            ELSE aira_users.last_incoming_at
                        END,

                    last_owner_message_at =
                        CASE
                            WHEN $3
                            THEN NOW()
                            ELSE aira_users.last_owner_message_at
                        END,

                    last_auto_reply_at =
                        CASE
                            WHEN $4
                            THEN NOW()
                            ELSE aira_users.last_auto_reply_at
                        END,

                    last_urgent_reply_at =
                        CASE
                            WHEN $5
                            THEN NOW()
                            ELSE aira_users.last_urgent_reply_at
                        END,

                    updated_at = NOW()
            """,
                int(user_id),
                bool(incoming),
                bool(owner_message),
                bool(auto_reply),
                bool(urgent_reply)
            )

    async def set_aira_conversation_state(
        self,
        user_id: int,
        *,
        offline_reply_stage: Optional[int] = None,
        reset_conversation: bool = False
    ):
        """
        Update AIRA-only conversation state.
        """

        if not self.pool:
            raise RuntimeError(
                "Database pool not initialized"
            )

        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO aira_users (
                    user_id,
                    conversation_started_at,
                    offline_reply_stage,
                    updated_at
                )
                VALUES (
                    $1,
                    NOW(),
                    COALESCE($2, 0),
                    NOW()
                )

                ON CONFLICT (user_id)
                DO UPDATE SET

                    conversation_started_at =
                        CASE
                            WHEN $3
                            THEN NOW()
                            ELSE COALESCE(
                                aira_users.conversation_started_at,
                                NOW()
                            )
                        END,

                    offline_reply_stage =
                        CASE
                            WHEN $2::INTEGER IS NOT NULL
                            THEN $2
                            ELSE aira_users.offline_reply_stage
                        END,

                    updated_at = NOW()
            """,
                int(user_id),
                offline_reply_stage,
                bool(reset_conversation)
            )

    async def save_aira_topic(
        self,
        user_id: int,
        topic_id: int,
        topic_name: str
    ):
        """
        Persist the private AIRA Inbox topic mapping.

        Phase 4 will use this.
        """

        if not self.pool:
            raise RuntimeError(
                "Database pool not initialized"
            )

        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO aira_users (
                    user_id,
                    topic_id,
                    topic_name,
                    updated_at
                )
                VALUES (
                    $1,
                    $2,
                    $3,
                    NOW()
                )

                ON CONFLICT (user_id)
                DO UPDATE SET
                    topic_id = EXCLUDED.topic_id,
                    topic_name = EXCLUDED.topic_name,
                    updated_at = NOW()
            """,
                int(user_id),
                int(topic_id),
                str(topic_name or "")[:128]
            )    
                    

# Global database instance
db = Database()
