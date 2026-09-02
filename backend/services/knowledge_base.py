"""
Knowledge Base Service
Manages the user's "Bio-Data Map" for form filling.
Loads from config/user_questions_template.yaml and learns new answers.
"""

import yaml
import os
from pathlib import Path
from typing import Dict, Any, Optional

class KnowledgeBase:
    def __init__(self, template_path: str = "config/user_questions_template.yaml", learned_path: str = "config/user_data_map.yaml"):
        self.base_dir = Path(__file__).parent.parent.parent
        self.template_path = self.base_dir / template_path
        self.learned_path = self.base_dir / learned_path
        self.data: Dict[str, Any] = {}
        self.load_data()

    def load_data(self):
        """Loads data from template and learned files."""
        # Load template (base truth)
        if self.template_path.exists():
            with open(self.template_path, 'r', encoding='utf-8') as f:
                self.data.update(yaml.safe_load(f) or {})
        
        # Load learned data (overrides or adds to template)
        if self.learned_path.exists():
            with open(self.learned_path, 'r', encoding='utf-8') as f:
                learned = yaml.safe_load(f) or {}
                self.data.update(learned)

    def get_answer(self, key: str) -> Optional[str]:
        """Retrieves an answer by key."""
        return self.data.get(key)

    def search_answer(self, query: str) -> Optional[str]:
        """
        Semantic search for an answer (Placeholder for Vector DB).
        For now, does a fuzzy key match or exact match.
        """
        query = query.lower().strip()
        
        # Exact match attempt
        if query in self.data:
            return self.data[query]
        
        # Fuzzy match / key contains
        for key, value in self.data.items():
            if key.lower() in query or query in key.lower():
                return value
        
        # Value match (maybe the user typed the answer as the key by mistake? Unlikely but possible)
        return None

    def save_learned_answer(self, question: str, answer: str):
        """Saves a new question-answer pair to the learned map."""
        self.data[question] = answer
        
        # Load existing learned data
        learned_data = {}
        if self.learned_path.exists():
            with open(self.learned_path, 'r', encoding='utf-8') as f:
                learned_data = yaml.safe_load(f) or {}
        
        # Update and save
        learned_data[question] = answer
        with open(self.learned_path, 'w', encoding='utf-8') as f:
            yaml.dump(learned_data, f, default_flow_style=False)

# Global instance
kb = KnowledgeBase()
