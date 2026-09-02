"""
Applier Service
Orchestrates the application process using Playwright and the Inference Engine.
"""

import asyncio
import time
from typing import Dict, Any, List
from playwright.async_api import async_playwright, Page

from backend.services.inference import inference
from backend.services.knowledge_base import kb
from backend.websocket_manager import ws_manager

class ApplierService:
    def __init__(self):
        self.browser = None
        self.context = None
        self.page = None

    async def start_browser(self):
        """Starts the Playwright browser."""
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=False) # Visual mode for debugging
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()

    async def close_browser(self):
        """Closes the browser."""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    async def apply_to_job(self, job_url: str):
        """
        Main method to apply to a job.
        1. Navigate
        2. Extract Form
        3. Map Fields
        4. Fill
        5. Submit (or Verify)
        """
        if not self.browser:
            await self.start_browser()

        try:
            await self.page.goto(job_url, timeout=60000)
            await self.page.wait_for_load_state("networkidle")
            
            # 1. Detect Form Fields (Simplified - gathering inputs)
            # In a real scenario, we'd use more robust selectors or even accessibility tree
            inputs = await self.page.evaluate('''() => {
                return Array.from(document.querySelectorAll('input, select, textarea')).map(el => {
                    return {
                        tag: el.tagName,
                        type: el.type,
                        name: el.name,
                        id: el.id,
                        placeholder: el.placeholder,
                        label: document.querySelector(`label[for="${el.id}"]`)?.innerText || el.parentElement.innerText
                    }
                });
            }''')
            
            # 2. Map Fields using LLM
            # Simplify the input for the LLM to save tokens
            simplified_inputs = [
                f"Tag: {i['tag']}, Type: {i['type']}, Name: {i['name']}, Label: {i['label'][:50]}" 
                for i in inputs if i['type'] != 'hidden'
            ]
            
            form_context = "\n".join(simplified_inputs)
            
            # Get existing keys from KB
            user_keys = list(kb.data.keys())
            
            mapping = inference.map_form_fields(form_context, user_keys)
            
            # 3. Fill Fields
            for input_data in inputs:
                input_name = input_data.get('name') or input_data.get('id')
                if not input_name: continue
                
                user_key = mapping.get(input_name)
                
                if user_key and kb.get_answer(user_key):
                    value = kb.get_answer(user_key)
                    await self._fill_field(input_data, value)
                elif user_key:
                     # Known key but missing value, or explicitly mapped to a new key
                     print(f"Input required for {user_key} ({input_data['label']})")
                     # Ask user via WebSocket
                     answer = await self._ask_user(user_key, input_data['label'])
                     if answer:
                         await self._fill_field(input_data, answer)
                else:
                    # Totally unknown field, try to ask user with a generated key
                    # Generate a key based on label
                    generated_key = input_data['label'].lower().replace(' ', '_')[:30]
                    print(f"Unknown field: {input_name} ({input_data['label']}) - Asking user")
                    answer = await self._ask_user(generated_key, input_data['label'])
                    if answer:
                         await self._fill_field(input_data, answer)
            
            # 4. Upload Resume
            # ...
            
            return True

        except Exception as e:
            print(f"Application Error: {e}")
            return False

    async def _ask_user(self, key: str, label: str) -> str:
        """
        Pauses and asks the user for input via WebSocket.
        Polls the KnowledgeBase for the answer.
        """
        print(f"Requesting user input for: {key}")
        await ws_manager.send_input_request(key, label)
        
        # Poll for answer
        # Timeout after 5 minutes (300 seconds)
        for _ in range(600):
            if kb.get_answer(key):
                return kb.get_answer(key)
            await asyncio.sleep(0.5)
            
        print(f"Timed out waiting for user input for {key}")
        return None

    async def _fill_field(self, input_metadata: Dict, value: str):
        """Fills a single field based on type."""
        # Selector strategy: try name, then id, then placeholder, then label
        selector = None
        if input_metadata.get('name'):
            selector = f"input[name='{input_metadata['name']}'], select[name='{input_metadata['name']}'], textarea[name='{input_metadata['name']}']"
        elif input_metadata.get('id'):
            selector = f"#{input_metadata['id']}"
            
        if not selector:
            print(f"Could not determine selector for {input_metadata}")
            return

        try:
            # Handle different input types
            tag = input_metadata.get('tag', '').upper()
            inputType = input_metadata.get('type', '').lower()
            
            if tag == 'SELECT':
                await self.page.select_option(selector, label=str(value))
            elif inputType == 'checkbox':
                if str(value).lower() in ['true', 'yes', '1', 'on']:
                    await self.page.check(selector)
                else:
                    await self.page.uncheck(selector)
            elif inputType == 'radio':
                # For radio, we might need to find the specific input with the value
                # This is tricky without more context, simplistic approach:
                await self.page.click(selector) 
            elif inputType == 'file':
                 # Resume upload handling would go here
                 pass
            else:
                await self.page.fill(selector, str(value))
                
            print(f"Filled {input_metadata['name'] or input_metadata['id']} with {value}")
            
        except Exception as e:
            print(f"Error filling {selector}: {e}")

# Global instance
applier = ApplierService()
