"""
Ask your own key which models it can see, so nobody has to guess a model ID.

    python models.py
    OPENAI_MODEL=gpt-6-astra python models.py     # also checks that one works
"""
import os
import sys

from openai import OpenAI

client = OpenAI()

ids = sorted(m.id for m in client.models.list())
print(f"{len(ids)} models visible to this key:\n")
for mid in ids:
    print(" ", mid)

want = os.environ.get("OPENAI_MODEL", "gpt-6.1-sol")
print(f"\nmini-claude would use: {want}")
if want in ids:
    print("-> available")
else:
    print("-> NOT in the list above. Set OPENAI_MODEL to one that is.")
    sys.exit(1)
