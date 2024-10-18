# api
The API for Nova Search

## Setting up and running the API

1. Cloning the repository
```
git clone https://github.com/Nova-Search/api
cd api
git submodule update --init --recursive
```

2. Create a virtual environment (optional but recommended)
```
python -m venv venv
source venv/bin/activate  # On Windows use `venv\Scripts\activate`
```

3. Install required dependencies
```
pip install -r requirements.txt
```

4. Run the API
```
uvicorn main:app --reload
```
This will start the API on `http://127.0.0.1:8000`

If you do not have an existing `links.db` database, the API will throw a warning and ask you if you want to create it, in most cases you'd choose yes, otherwise choose no and panic trying to find your missing database.

5. Testing the API (optional)

Run the following command to test the API

```
curl -X 'GET' \
  'http://127.0.0.1:8000/search?query=example' \
  -H 'accept: application/json'
```

## Crawling Sites for Pages

This repository includes a submodule for the Nova Search crawler. To use it, follow these steps:

1. Initialize and update the submodule
    ```sh
    git submodule update --init --recursive
    ```

2. Navigate to the crawler directory
    ```sh
    cd crawler
    ```

3. Install the required dependencies
    ```sh
    pip install -r requirements.txt
    ```

4. Run the crawler
    ```sh
    python web.py
    ```

The crawler will ask you for a URL to start with, a depth for URLs to crawl, then start fetching pages from the specified sites.