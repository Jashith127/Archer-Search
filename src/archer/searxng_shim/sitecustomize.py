"""Copied into SearXNG's own venv by archer.searxng_local; Python imports it in every process there.

curl_cffi needs the selector event loop's add_reader. Under Windows' default Proactor loop it starts an
extra selector thread and warns at every start.
"""
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
