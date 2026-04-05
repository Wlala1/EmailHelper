import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# 解决跨域问题，方便前端调用
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db_stats():
    conn = psycopg2.connect(
        dbname="fetched_data", user="ouma_user", 
        password="ouma_password", host="localhost"
    )
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        query = """
            SELECT 
                classification->>'topic' as label, 
                COUNT(*) as value 
            FROM emails 
            WHERE classification IS NOT NULL 
            GROUP BY 1 ORDER BY value DESC;
        """
        cur.execute(query)
        return cur.fetchall()
    finally:
        conn.close()

@app.get("/api/stats")
async def stats():
    return get_db_stats()