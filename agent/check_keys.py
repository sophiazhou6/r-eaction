"""Check that the API keys in .env work. Prints OK/FAIL, never the keys.
    uv run python -m agent.check_keys
"""
import os

from dotenv import load_dotenv

load_dotenv()
k = os.environ.get("OPENAI_API_KEY", "")
print("OpenAI key in .env ends with:", "..." + k[-4:] if k else "(missing)")
try:
    from openai import OpenAI
    OpenAI().chat.completions.create(model="gpt-4.1-mini", max_tokens=3,
                                     messages=[{"role": "user", "content": "hi"}])
    print("OPENAI    OK")
except Exception as e:
    print("OPENAI    FAIL", str(e)[:160])
try:
    from langchain_anthropic import ChatAnthropic
    ChatAnthropic(model="claude-haiku-5-5", max_tokens=3).invoke("hi")
    print("ANTHROPIC OK")
except Exception as e:
    print("ANTHROPIC FAIL", str(e)[:160])
