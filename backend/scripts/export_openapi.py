"""Write the FastAPI OpenAPI schema: python scripts/export_openapi.py > openapi.json"""
import json

from app.main import app

print(json.dumps(app.openapi(), indent=2))
