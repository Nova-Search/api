# api
The API for Nova Search

## Setting up and running the API

1. Cloning the repository
```
git clone https://github.com/Nova-Search/api
cd api
```

2. Create a virtual environment (optional but recommended)
```
python -m venv venv
source venv/bin/activate  # On Windows use `venv\Scripts\activate`
```

3. Install required dependencies
```
pip install fastapi uvicorn sqlite3
```

4. Run the API
```
uvicorn main:app --reload
```

This will start the API on http://127.0.0.1:8000

5. Testing the API (optional)

Run the following command to test the API

```
curl -X 'GET' \
  'http://127.0.0.1:8000/search?query=example' \
  -H 'accept: application/json'
```