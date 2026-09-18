"""GridWise AI Energy Copilot — a conversational layer over existing
GridWise services (optimizer, interpreter, simulator, explainability,
system status). This package contains no optimization or LLM-provider
logic of its own: it classifies intent, calls the existing pipelines
directly, and formats their real results into a chat reply. See
copilot_agent.py for the orchestration entrypoint.
"""
