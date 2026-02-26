import sys
import os
from importlib import import_module
import traceback

# Ensure project root is on sys.path so we can import `main` when running from scripts/
root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if root not in sys.path:
    sys.path.insert(0, root)

try:
    m = import_module('main')
    api_key = m.get_api_key_from_file()
    bot = m.EnhancedFitBot(api_key) if api_key else None
    if not bot:
        print('FitBot not initialized (check API key).')
    elif not getattr(bot, 'rag_system', None):
        print('RAG system not present on bot.')
    else:
        print('Starting RAG initialize_system(force_recreate=True)...')
        bot.rag_system.initialize_system(force_recreate=True)
        print('Done. Check logs for details.')
except Exception as e:
    print('Error while recreating RAG:')
    traceback.print_exc()
