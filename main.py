from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3
from datetime import datetime, timedelta
import os
import sys

if __name__ == "__main__":
    print("\033[1;34mYou ran the API wrong; check README.md for more info\033[0m")
    sys.exit(1)

app = FastAPI()

# Constants
DB_PATH = 'links.db'
FAVICON_DIR = 'favicons'  # Directory where favicons are stored

# Dictionary to store request counts and timestamps for rate limiting
request_counts = {}

# Rate limit configuration
RATE_LIMITS = {
    "GET": 30,  # requests per minute
    "POST": 10  # requests per minute
}
TIME_WINDOW = timedelta(minutes=1)  # time window

def check_db_exists():
    """Check if the database exists."""
    if not os.path.exists(DB_PATH):
        print("\033[1;33mWarning: Database does not exist.\033[0m")
        choice = input("Do you want to recreate the database? (yes/no): ").strip().lower()
        if choice == 'yes':
            create_db()
        else:
            print("Stopping API.")
            sys.exit(1)

def create_db():
    """Create the database and necessary tables."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE pages (
            id INTEGER PRIMARY KEY,
            url TEXT NOT NULL,
            title TEXT,
            description TEXT,
            keywords TEXT,
            priority INTEGER DEFAULT 0,
            favicon_id TEXT
        )
    ''')
    conn.commit()
    conn.close()
    print("Database created successfully.")

# Check if the database exists at startup
check_db_exists()

# Rate limit middleware
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    ip = request.headers.get("CF-Connecting-IP", request.client.host)
    now = datetime.now()
    method = request.method

    # Exempt /favicon endpoint from rate limiting
    if request.url.path.startswith("/favicon"):
        return await call_next(request)

    if ip not in request_counts:
        request_counts[ip] = {}

    if method not in request_counts[ip]:
        request_counts[ip][method] = {"count": 1, "timestamp": now}
    else:
        request_info = request_counts[ip][method]
        if now - request_info["timestamp"] > TIME_WINDOW:
            request_counts[ip][method] = {"count": 1, "timestamp": now}
        else:
            request_info["count"] += 1
            if request_info["count"] > RATE_LIMITS[method]:
                return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})

    response = await call_next(request)
    return response

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db_connection():
    """Establish a new database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.get("/search")
def search(query: str = Query(...)):
    """Search the database by title, description, keywords, and URL."""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            '''
            SELECT url, title, description, keywords, priority, favicon_id
            FROM pages
            WHERE 
                title LIKE ? OR 
                description LIKE ? OR 
                keywords LIKE ? OR 
                url LIKE ?
            ORDER BY 
                CASE 
                    WHEN title LIKE ? THEN 1
                    WHEN description LIKE ? THEN 2
                    WHEN keywords LIKE ? THEN 3
                    ELSE 4
                END, 
                priority DESC
            ''',
            (
                f'%{query}%', f'%{query}%', f'%{query}%', f'%{query}%',
                f'%{query}%', f'%{query}%', f'%{query}%'
            )
        )

        results = cursor.fetchall()
        if not results:
            raise HTTPException(status_code=404, detail="No results found.")

        return [
            {
                "url": row["url"],
                "title": row["title"],
                "description": row["description"],
                "keywords": row["keywords"],
                "favicon_id": row["favicon_id"]
            }
            for row in results
        ]

    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        conn.close()

class ClickData(BaseModel):
    url: str

def update_priority(url: str, change: int):
    """Update the priority of a page by a given change."""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute('UPDATE pages SET priority = priority + ? WHERE url = ?', (change, url))
        conn.commit()
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        conn.close()

@app.post("/quality/rateresult/pageclick")
def increase_priority(data: ClickData):
    """Increase the priority of a page by 1."""
    update_priority(data.url, 1)
    return {"detail": "Priority updated"}

@app.post("/quality/rateresult/bad")
def decrease_priority(data: ClickData):
    """Decrease the priority of a page by 2."""
    update_priority(data.url, -2)
    return {"detail": "Thank you for your feedback!"}

@app.post("/quality/rateresult/good")
def increase_priority_by_two(data: ClickData):
    """Increase the priority of a page by 2."""
    update_priority(data.url, 2)
    return {"detail": "Thank you for your feedback!"}

@app.get("/favicon/{favicon_id}")
def get_favicon(favicon_id: str):
    """Retrieve a favicon by its ID."""
    extensions = ['ico', 'png', 'svg', 'jpg', 'webp']
    for ext in extensions:
        favicon_path = os.path.join(FAVICON_DIR, f"{favicon_id}.{ext}")
        if os.path.exists(favicon_path):
            return FileResponse(favicon_path)

    raise HTTPException(status_code=404, detail="Favicon not found.")