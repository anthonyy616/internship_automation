# New Implementation: Adaptive Autonomous Agent

## Core Concept

Instead of hardcoding every possible form field, we build a **Semantic Mapper**.
Map: `{"Question Text / Context": "Canonical Answer Key"}`.

## System Components

### 1. The Knowledge Base (`config/user_data_map.yaml`)

A growing dictionary of questions the user has answered.

```yaml
# Canonical Examples
"What is your full name?": "Anthony Ogbuah"
"Name": "Anthony Ogbuah"

# Learned Examples
"Please provide your mobile number": "+1234567890"
"Cell Phone": "+1234567890"
```

### 2. The Inference Engine (`backend/services/inference.py`)

Uses LLM to match new form fields to known keys.
Input: `<label>Mobile</label> <input type="tel">`
Prompt: "Map 'Mobile' to a known key in User Profile."
Output: `phone_number`

### 3. The Fallback User Loop

If Confidence < 80%:

1. Bot Pauses.
2. Sends WebSocket generic event: `{"type": "INPUT_REQUIRED", "question": "What is your 'Notice Period'?"}`.
3. User answers in UI: "2 weeks".
4. Bot resumes & saves `("Notice Period" -> "2 weeks")` to Map.

## Development Phases

### Phase 1: Foundation

- Create `user_questions_template.yaml` (The "Seed" Map).
- Update `config.py` to load this map.

### Phase 2: The Agent

- Create `ApplierAgent` using Playwright.
- Implement `extract_form_fields(page)` -> JSON.

### Phase 3: The Brain

- Implement `match_field_value(field, user_map)`.
- Implement `ask_user_for_input(field)`.

### Phase 4: Integration

- Connect to Orchestrator.
- Enable "Guaranteed Mode" (Wait for user > Skip).
