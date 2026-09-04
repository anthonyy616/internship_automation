"""
Inference Engine
Uses LLM to map form fields to the user's bio-data map.
"""

import json
import logging
from typing import Dict, Any, List, Optional
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

from backend.config import settings

logger = logging.getLogger(__name__)


class InferenceEngine:
    """
    LLM-based form-field mapper.

    Failures are never fatal: `map_form_fields` returns {} on any error and
    records the reason in `self.last_error` so callers can surface it (e.g.
    "OpenAI credits exhausted") instead of the bot silently going blind.
    """

    def __init__(self):
        self.llm = ChatOpenAI(
            model=settings.openai_model,
            temperature=0.0,
            api_key=settings.openai_api_key
        )
        self.last_error: Optional[str] = None

    def map_form_fields(self, form_html: str, user_data_keys: List[str]) -> Dict[str, str]:
        """
        Analyzes a form's HTML (simplified) and maps inputs to user profile keys.
        Returns: {"field_name_or_id": "user_profile_key"}
        """
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", """
            You are an expert form filler. Your goal is to map HTML form inputs to a user's profile keys.
            
            User Profile Keys Available:
            {user_keys}
            
            Rules:
            1. Analyze the provided HTML context for each input (label, placeholder, name, id).
            2. specific inputs like 'resume' or 'cv' should map to 'resume_path'.
            3. If a field is not in the user keys but is a standard question (e.g. "Gender"), try to map it to a close key or leave it null if totally unknown.
            4. Return a JSON object where keys are the input's 'name' or 'id' attribute, and values are the User Profile Key.
            5. If you are unsure, set the value to null.
            """),
            ("user", "Form HTML Context:\n{form_html}")
        ])
        
        chain = prompt | self.llm | JsonOutputParser()

        try:
            result = chain.invoke({
                "user_keys": ", ".join(user_data_keys),
                "form_html": form_html
            })
            self.last_error = None
            return result or {}
        except Exception as e:
            self.last_error = str(e)
            logger.error("Inference Error: %s", e)
            return {}

# Global instance
inference = InferenceEngine()
