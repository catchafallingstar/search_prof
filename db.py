from builtins import Exception
import os
import psycopg2
import streamlit as st
from dotenv import load_dotenv
import pandas as pd
from sqlalchemy import text
from sqlalchemy import create_engine
# 加载 .env 文件的环境变量
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

@st.cache_resource
def get_sqlalchemy_engine():
    """Cloud-ready SQLAlchemy engine loader."""
    # 1. Try single DATABASE_URL string (used by Supabase, Neon, Render, Heroku)
    db_url = os.getenv("DATABASE_URL")

    if db_url:
        # Fix legacy 'postgres://' URIs for SQLAlchemy compatibility
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
    else:
        # 2. Fall back to individual env variables (Local development)
        db_user = os.getenv("DB_USER", "postgres")
        db_pass = os.getenv("DB_PASS", "postgres")
        db_host = os.getenv("DB_HOST", "localhost")
        db_port = os.getenv("DB_PORT", "5432")
        db_name = os.getenv("DB_NAME", "scholar_radar")
        db_url = f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"

    # Add SSL requirement for cloud database connections
    connect_args = {}
    if os.getenv("DB_HOST") != "localhost" and "localhost" not in db_url:
        connect_args["sslmode"] = "require"

    return create_engine(db_url, connect_args=connect_args)

def fetch_radar_dataframe(engine, session_id):
    query = text("""
        SELECT p.id AS prof_id, p.name, p.institution, COALESCE(p.hiring_score, 0) AS hiring_score, 
               p.homepage_url, p.research_domain, p.career_stage,
               hs.signal_type, hs.raw_text, hs.confidence_score, hs.source_url
        FROM professors p
        LEFT JOIN hiring_signals hs ON p.id = hs.professor_id
        WHERE p.session_id = :session_id
        ORDER BY p.hiring_score DESC, p.id DESC;
    """)
    return pd.read_sql(query, engine, params={"session_id": session_id}).fillna("")

def get_db_connection():
    """
    建立并返回数据库连接的通用函数
    """
    if not DATABASE_URL:
        raise ValueError("❌ 错误：未在 .env 文件中找到 DATABASE_URL，请检查配置！")
    
    return psycopg2.connect(DATABASE_URL)

# 当你直接运行 `python db.py` 时，会执行下面的测试逻辑；
# 如果是被其他脚本 import，则不会触发测试。
if __name__ == "__main__":
    try:
        conn = get_db_connection()
        print("🚀 数据库连接测试成功！")
        
        # 顺便测试一下之前建的表是否存在
        cursor = conn.cursor()
        cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public';")
        tables = cursor.fetchall()
        print(f"📊 当前数据库中的表数量: {len(tables)} 张")
        
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"❌ 数据库连接失败: {e}")
        
        
# In db.py
def auto_migrate_db(conn):
    try:
        with conn.cursor() as cur:
            # 1. Add session_id column to BOTH tables first
            cur.execute("""
                ALTER TABLE professors ADD COLUMN IF NOT EXISTS session_id VARCHAR(255);
                ALTER TABLE papers ADD COLUMN IF NOT EXISTS session_id VARCHAR(255);
            """)
            
            # 2. Drop legacy indexes/constraints on openalex_id that cause crashes
            cur.execute("""
                DROP INDEX IF EXISTS prof_openalex_idx;
                ALTER TABLE professors DROP CONSTRAINT IF EXISTS prof_openalex_idx CASCADE;
                ALTER TABLE professors DROP CONSTRAINT IF EXISTS professors_openalex_id_key CASCADE;
            """)

            # 3. Update unique constraints scoped by session_id
            cur.execute("""
                ALTER TABLE professors DROP CONSTRAINT IF EXISTS unique_prof_inst CASCADE;
                ALTER TABLE professors DROP CONSTRAINT IF EXISTS unique_prof_inst_session CASCADE;
                ALTER TABLE professors ADD CONSTRAINT unique_prof_inst_session UNIQUE (name, institution, session_id);
                
                ALTER TABLE papers DROP CONSTRAINT IF EXISTS papers_openalex_id_key CASCADE;
                ALTER TABLE papers DROP CONSTRAINT IF EXISTS unique_paper_session CASCADE;
                ALTER TABLE papers ADD CONSTRAINT unique_paper_session UNIQUE (openalex_id, session_id);
            """)

            # 3.1 hiring_signals needs a raw_text_hash column + unique index so that
            # save_signal_to_db()'s "ON CONFLICT (raw_text_hash) DO NOTHING" actually
            # works instead of throwing UndefinedColumn and silently rolling back
            # every signal that gets found.
            cur.execute("""
                ALTER TABLE hiring_signals ADD COLUMN IF NOT EXISTS raw_text_hash VARCHAR(64);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_hash ON hiring_signals(raw_text_hash);
            """)

            # 4. Auto-cleanup: Delete records older than 2 hours
            cur.execute("""
                DELETE FROM professors WHERE created_at < NOW() - INTERVAL '2 HOURS';
                DELETE FROM papers WHERE created_at < NOW() - INTERVAL '2 HOURS';
            """)
            
            conn.commit()
            print("Database migration & cleanup completed successfully.")
    except Exception as e:
        conn.rollback()
        print(f"Error migrating database: {e}")