# connect to postgresql database

from psycopg2 import connect
from psycopg2.extras import DictCursor
from psycopg2.pool import ThreadedConnectionPool
import os
import logging

logger = logging.getLogger(__name__)

# Global connection pool — shared across all threads/requests
_pool = None

def _get_pool():
    global _pool
    if _pool is None or _pool.closed:
        _pool = ThreadedConnectionPool(
            minconn=2,
            maxconn=20,
            host=os.getenv('DB_HOST', 'localhost'),
            port=5432,
            user='postgres',
            password=os.getenv('DB_PASSWORD', ''),
            database='postgres'
        )
        logger.info(f"Connection pool created (min=2, max=20)")
    return _pool


class Database:

    def __init__(self):
        pool = _get_pool()
        self.connection = pool.getconn()
        self.connection.autocommit = False
        self.cursor = self.connection.cursor(cursor_factory=DictCursor)

    def close(self):
        """Return connection back to the pool."""
        try:
            self.cursor.close()
        except Exception:
            pass
        try:
            _get_pool().putconn(self.connection)
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.connection.commit()
        else:
            self.connection.rollback()
        self.close()

    def test_connection(self):
        self.cursor.execute("SELECT version();")
        record = self.cursor.fetchone()
        print("You are connected to - ", record, "\n")

    def execute_query(self, query: str):
        self.cursor.execute(query)
        result = self.cursor.fetchall()
        self.close()
        return result

    def execute_query_with_params(self, query: str, params: tuple):
        self.cursor.execute(query, params)
        result = self.cursor.fetchall()
        self.close()
        return result

    def execute_query_with_params_and_fetch_one(self, query: str, params: tuple):
        self.cursor.execute(query, params)
        result = self.cursor.fetchone()
        self.close()
        return result

    def execute_query_with_params_and_fetch_all(self, query: str, params: tuple):
        self.cursor.execute(query, params)
        result = self.cursor.fetchall()
        self.close()
        return result

    def insert_query(self, query: str, params: tuple):
        self.cursor.execute(query, params)
        self.connection.commit()
        self.close()

    def create_table_job_details(self):
        query = """
        CREATE TABLE IF NOT EXISTS job_details(
            job_id VARCHAR(255) PRIMARY KEY,
            title VARCHAR(255),
            location VARCHAR(255),
            department VARCHAR(255),
            summary TEXT,
            long_description TEXT,
            date DATE,
            end_date DATE DEFAULT NULL,
            url VARCHAR(255)
        );
        """
        self.cursor.execute(query)
        self.connection.commit()
        self.close()
        print("Table created successfully")

    def create_table_companies_config(self):
        query = """
        CREATE TABLE IF NOT EXISTS companies_config (
            id SERIAL PRIMARY KEY,
            company_name VARCHAR(255) UNIQUE NOT NULL,
            base_url VARCHAR(1024) NOT NULL,
            job_list_selector TEXT,
            title_selector TEXT,
            link_selector TEXT,
            job_id_selector TEXT,
            job_title_selector TEXT,
            location_selector TEXT,
            department_selector TEXT,
            summary_selector TEXT,
            long_description_selector TEXT,
            date_selector TEXT
        );
        """
        self.cursor.execute(query)
        self.connection.commit()
        
        seed_query = """
        INSERT INTO companies_config (
            company_name, base_url, job_list_selector, title_selector, link_selector,
            job_id_selector, job_title_selector, location_selector, department_selector,
            summary_selector, long_description_selector, date_selector
        ) VALUES (
            'Apple', 
            'https://jobs.apple.com/en-us/search?location=united-states-USA&page', 
            'div.job-list-item', 'h3 a', 'h3 a',
            '#jobdetails-jobnumber', '#jobdetails-postingtitle', '#jobdetails-joblocation', 
            '#jobdetails-teamname', '#jobdetails-jobdetails-jobsummary-content-row > span', 
            '#jobdetails-jobdetails-jobdescription-content-row > span', '#jobdetails-jobpostdate'
        ) ON CONFLICT (company_name) DO NOTHING;
        """
        self.cursor.execute(seed_query)
        self.connection.commit()
        self.close()
        
        print("Table companies_config created successfully")


# Initialize tables on import — use a single connection for all setup steps
def _init_db():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        cur = conn.cursor()
        # Test
        cur.execute("SELECT version();")
        print("DB connected:", cur.fetchone()[0][:30])

        # job_details table
        cur.execute("""
        CREATE TABLE IF NOT EXISTS job_details(
            job_id VARCHAR(255) PRIMARY KEY,
            title VARCHAR(255),
            location VARCHAR(255),
            department VARCHAR(255),
            summary TEXT,
            long_description TEXT,
            date DATE,
            end_date DATE DEFAULT NULL,
            url VARCHAR(255)
        );
        """)

        # companies_config table + seed
        cur.execute("""
        CREATE TABLE IF NOT EXISTS companies_config (
            id SERIAL PRIMARY KEY,
            company_name VARCHAR(255) UNIQUE NOT NULL,
            base_url VARCHAR(1024) NOT NULL,
            job_list_selector TEXT,
            title_selector TEXT,
            link_selector TEXT,
            job_id_selector TEXT,
            job_title_selector TEXT,
            location_selector TEXT,
            department_selector TEXT,
            summary_selector TEXT,
            long_description_selector TEXT,
            date_selector TEXT
        );
        """)
        cur.execute("""
        INSERT INTO companies_config (
            company_name, base_url, job_list_selector, title_selector, link_selector,
            job_id_selector, job_title_selector, location_selector, department_selector,
            summary_selector, long_description_selector, date_selector
        ) VALUES (
            'Apple',
            'https://jobs.apple.com/en-us/search?location=united-states-USA&page',
            'div.job-list-item', 'h3 a', 'h3 a',
            '#jobdetails-jobnumber', '#jobdetails-postingtitle', '#jobdetails-joblocation',
            '#jobdetails-teamname', '#jobdetails-jobdetails-jobsummary-content-row > span',
            '#jobdetails-jobdetails-jobdescription-content-row > span', '#jobdetails-jobpostdate'
        ) ON CONFLICT (company_name) DO NOTHING;
        """)

        conn.commit()
        cur.close()
        print("Tables initialized successfully.")
    except Exception as e:
        conn.rollback()
        print(f"DB init error: {e}")
    finally:
        pool.putconn(conn)

_init_db()
