# Fixes & Problems

## Current Problems

1. **Placeholder Application Logic**: The bot currently only marks jobs as "Applied" in the database without actually interacting with the company website.
2. **Missing Form Data**: The `config.yaml` lacks critical information (Phone, Address, Diversity Info) required for 99% of applications.
3. **Rigid Logic**: Simple script-based filling fails when field names vary slightly (e.g., "Phone" vs "Mobile Number").
4. **No Feedback Loop**: If the bot gets stuck, it fails silently or skips, rather than asking for help.

## Proposed Fixes

1. **Implement Adaptive Applier**: Use Playwright + LLM to analyze forms dynamically.
2. **Interactive Mode**: If a field is unknown, prompt the user via WebSocket/UI to provide the answer.
3. **Learning System**: Save user answers to `config/user_data_map.yaml` so the bot learns "Mobile Number" = "Phone".
4. **Expanded Profile**: Create a comprehensive `user_questions_template.yaml` for the user to fill out upfront.

## Technical Debt

- Hardcoded selectors in `tools.py` need to be replaced with semantic selectors found by LLM.
- `orchestrator.py` needs to handle "PAUSED" state for user input.
