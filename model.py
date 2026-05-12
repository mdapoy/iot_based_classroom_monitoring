from google import genai
from dotenv import load_dotenv
import os

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")

if not API_KEY:
    raise Exception("GEMINI_API_KEY not found")

client = genai.Client(api_key=API_KEY)

print("=" * 50)
print("AVAILABLE GEMINI MODELS")
print("=" * 50)

try:
    models = client.models.list()

    for model in models:
        print(f"\nMODEL NAME : {model.name}")

        if hasattr(model, "display_name"):
            print(f"DISPLAY    : {model.display_name}")

        if hasattr(model, "description"):
            print(f"DESC       : {model.description}")

        if hasattr(model, "input_token_limit"):
            print(f"INPUT TOK  : {model.input_token_limit}")

        if hasattr(model, "output_token_limit"):
            print(f"OUTPUT TOK : {model.output_token_limit}")

        print("-" * 50)

except Exception as e:
    print(f"ERROR: {e}")