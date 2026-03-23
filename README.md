# Job Harvestor 🍎

A robust, localized Producer-Consumer architecture for harvesting job listings (such as Apple Careers) into a PostgreSQL database, orchestrated via a Redis message queue.

## 🏗️ Architecture
- **Producer (`src/producer.py`)**: Launches a headless Google Chrome browser, iterates through job listing pages, extracts job URLs using CSS selectors, and queues them into a Redis list (`jobs`).
- **Consumer (`src/consumer.py`)**: Constantly pops URLs from the Redis `jobs` list, navigates to the detailed job pages, extracts the full job data, and inserts it into PostgreSQL.

---

## 🚀 Setup & Installation

### 1. Prerequisites
You must have the following installed on your system:
- Python 3.10+
- [PostgreSQL](https://postgresapp.com/) running locally
- [Redis](https://redis.io/docs/install/install-redis/) running locally
- Google Chrome installed at the default macOS location (`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`)

### 2. Environment Setup
Create and activate the virtual environment, then configure your environment variables:
```bash
# Activate your virtual environment
source .venv/bin/activate

# Set your PYTHONPATH to the root of the project
export PYTHONPATH=$(pwd)

# Set your PostgreSQL password (matches your local 'postgres' user)
export DB_PASSWORD="your_postgres_password"
```

### 3. PostgreSQL Setup
The scripts expect a local PostgreSQL instance running on port `5432` with the default user `postgres` and connecting to the `postgres` database.

You must initialize the database table first. Simply run the database script directly:
```bash
python3 src/Database/database.py
```
This will automatically connect to your database and execute the `CREATE TABLE IF NOT EXISTS job_details` command. Make sure you see "Table created successfully" in your terminal.

### 4. Redis Setup
Make sure your local Redis server is active. You can start it via Homebrew on Mac:
```bash
brew services start redis
```
*(The scripts will connect to Redis via `localhost:6379` by default).*

---

## 🛠️ Configuration (Setting Selectors)

If a target website (like Apple Careers) updates its interface, you will need to update the CSS selectors.

**How to find new selectors:**
1. Open the page in normal Google Chrome it and right-click -> **Inspect**.
2. Go to the **Console** tab and test queries like: `document.querySelectorAll('.your-guess')`
3. Once you find the correct class or ID that highlights the job rows or titles, update the `config` dictionaries in the Python scripts.

**Producer Selectors** (`src/producer.py`):
```python
    config = {
        'job_list_selector': "div.job-list-item",
        'title_selector': "h3 a",
        'link_selector': "h3 a",
    }
```

**Consumer Selectors** (`src/consumer.py`):
```python
    config = {
        "job_id": "#jobdetails-jobnumber",
        "title": "#jobdetails-postingtitle",
        "location": "#jobdetails-joblocation",
        ...
    }
```

---

## ▶️ Running the Pipeline

Once your database is created and Redis is active, you can run the pipeline. Since they are decoupled, you can run them in separate terminal tabs!

**Terminal 1 (Start the Producer to queue URLs):**
```bash
source .venv/bin/activate
export PYTHONPATH=$(pwd)
python3 -m src.producer
```

**Terminal 2 (Start the Consumer to process URLs and save to DB):**
```bash
source .venv/bin/activate
export PYTHONPATH=$(pwd)
export DB_PASSWORD="your_postgres_password"
python3 -m src.consumer
```
