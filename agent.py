#!/usr/bin/env python3

import asyncio
import json
import os
import re
import subprocess
import sys
import html
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


class DeepSeekParser:
    def __init__(self):
        self.email = None
        self.password = None
        self.page = None
        self.browser = None
        self.context = None
        self.playwright = None
        self.message_count = 0
        self.command_results = []
        self.current_dir = os.getcwd()
        self.last_checked_id = 2
        
        appdata = os.getenv('APPDATA')
        if not appdata:
            appdata = os.path.expanduser('~')
        self.config_dir = os.path.join(appdata, '.dopenagent')
        os.makedirs(self.config_dir, exist_ok=True)
        self.creds_file = os.path.join(self.config_dir, 'creds.json')

    def load_creds(self):
        try:
            if os.path.exists(self.creds_file):
                with open(self.creds_file, 'r', encoding='utf-8') as f:
                    creds = json.load(f)
                    self.email = creds.get('email', '')
                    self.password = creds.get('password', '')
                return True
        except Exception as e:
            print(f"[ERR] Load creds: {e}")
        return False

    def save_creds(self, email, password):
        try:
            with open(self.creds_file, 'w', encoding='utf-8') as f:
                json.dump({'email': email, 'password': password}, f, indent=2)
            print(f"[OK] Creds saved to {self.creds_file}")
            return True
        except Exception as e:
            print(f"[ERR] Save creds: {e}")
            return False

    def decode_html_entities(self, text):
        text = html.unescape(text)
        text = text.replace('&lt;', '<').replace('&gt;', '>')
        text = text.replace('&quot;', '"').replace('&amp;', '&')
        return text

    def parse_commands(self, text):
        text = self.decode_html_entities(text)
        commands = []
        
        command_pattern = r'<command>(.*?)</command>'
        write_file_pattern = r'<write_file\s+src="([^"]+)">(.*?)</write_file>'
        run_pattern = r'<run\s+logs="([^"]+)">(.*?)</run>'
        
        for match in re.finditer(command_pattern, text, re.DOTALL):
            commands.append({
                'type': 'command',
                'content': match.group(1).strip()
            })
        
        for match in re.finditer(write_file_pattern, text, re.DOTALL):
            commands.append({
                'type': 'write_file',
                'path': match.group(1),
                'content': match.group(2)
            })
        
        for match in re.finditer(run_pattern, text, re.DOTALL):
            commands.append({
                'type': 'run',
                'logs': match.group(1),
                'content': match.group(2).strip()
            })
        
        return commands

    def execute_command(self, cmd):
        try:
            result = subprocess.run(
                ["powershell", "-Command", cmd],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=self.current_dir
            )
            output = result.stdout if result.stdout else result.stderr
            return {
                'success': result.returncode == 0,
                'output': output,
                'code': result.returncode
            }
        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'output': 'Command timed out after 300 seconds',
                'code': -1
            }
        except Exception as e:
            return {
                'success': False,
                'output': str(e),
                'code': -1
            }

    def execute_run(self, cmd, log_file):
        try:
            log_path = os.path.join(self.current_dir, log_file)
            os.makedirs(os.path.dirname(log_path) if os.path.dirname(log_path) else '.', exist_ok=True)
            
            process = subprocess.Popen(
                ["powershell", "-Command", cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=self.current_dir
            )
            
            pid = process.pid
            
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write(f"[PID: {pid}] Process started\n")
                f.write(f"[CMD] {cmd}\n")
                f.write(f"[CWD] {self.current_dir}\n")
                f.write("="*60 + "\n\n")
                
                for line in iter(process.stdout.readline, ''):
                    f.write(line)
                    f.flush()
            
            process.wait()
            
            return {
                'success': process.returncode == 0,
                'pid': pid,
                'code': process.returncode,
                'log_file': log_path
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'log_file': log_file
            }

    def execute_write_file(self, path, content):
        try:
            full_path = os.path.join(self.current_dir, path)
            os.makedirs(os.path.dirname(full_path) if os.path.dirname(full_path) else '.', exist_ok=True)
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return {
                'success': True,
                'path': full_path,
                'size': len(content)
            }
        except Exception as e:
            return {
                'success': False,
                'path': path,
                'error': str(e)
            }

    async def process_commands(self, text):
        commands = self.parse_commands(text)
        results = []
        
        if not commands:
            return []
        
        print(f"\n[CMD] Found {len(commands)} commands to execute")
        
        for i, cmd in enumerate(commands, 1):
            print(f"\n[CMD #{i}] Executing: {cmd['type']}")
            
            if cmd['type'] == 'command':
                result = self.execute_command(cmd['content'])
                results.append({
                    'type': 'command',
                    'input': cmd['content'],
                    'result': result
                })
                if result['success']:
                    print(f"[CMD #{i}] Success")
                else:
                    print(f"[CMD #{i}] Failed with code {result['code']}")
                    if result['output']:
                        print(f"[CMD #{i}] Output: {result['output'][:200]}")
            
            elif cmd['type'] == 'write_file':
                result = self.execute_write_file(cmd['path'], cmd['content'])
                results.append({
                    'type': 'write_file',
                    'path': cmd['path'],
                    'result': result
                })
                if result['success']:
                    print(f"[CMD #{i}] File created: {cmd['path']} ({result['size']} bytes)")
                else:
                    print(f"[CMD #{i}] Failed: {result['error']}")
            
            elif cmd['type'] == 'run':
                result = self.execute_run(cmd['content'], cmd['logs'])
                results.append({
                    'type': 'run',
                    'cmd': cmd['content'],
                    'log_file': cmd['logs'],
                    'result': result
                })
                if result['success']:
                    print(f"[CMD #{i}] Process started with PID: {result['pid']}")
                    print(f"[CMD #{i}] Logs: {result['log_file']}")
                else:
                    print(f"[CMD #{i}] Failed: {result.get('error', 'Unknown error')}")
        
        return results

    async def init_browser(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-web-security',
                '--disable-features=IsolateOrigins,site-per-process'
            ]
        )
        self.context = await self.browser.new_context(
            viewport={'width': 1280, 'height': 720},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        self.page = await self.context.new_page()
        print("[OK] Browser started (headless)")

    async def login(self):
        try:
            await self.page.goto("https://chat.deepseek.com/sign_in", wait_until="domcontentloaded")
            print("[AUTH] Login page loaded")
            
            await asyncio.sleep(2)

            email_input = await self.page.wait_for_selector(
                'input[placeholder="Номер телефона / адрес электронной почты"]',
                timeout=15000
            )
            await email_input.click()
            await email_input.fill(self.email)
            print("[AUTH] Email entered")

            await asyncio.sleep(1)

            password_input = await self.page.wait_for_selector(
                'input[placeholder="Пароль"]',
                timeout=15000
            )
            await password_input.click()
            await password_input.fill(self.password)
            print("[AUTH] Password entered")

            await asyncio.sleep(1)

            login_button = await self.page.wait_for_selector(
                'span.ds-button__content:has-text("Войти")',
                timeout=15000
            )
            await login_button.click()
            print("[AUTH] Login button clicked")

            await asyncio.sleep(3)

            try:
                await self.page.wait_for_selector(
                    'textarea[placeholder="Сообщение для DeepSeek"]',
                    timeout=30000
                )
                print("[AUTH] Success, chat loaded")
                return True
            except:
                await self.page.screenshot(path=os.path.join(self.current_dir, "login_failed.png"))
                print("[AUTH] Chat not loaded, check screenshot login_failed.png")
                return False

        except PlaywrightTimeoutError as e:
            print(f"[AUTH] Error: {e}")
            return False

    async def send_message(self, message: str):
        try:
            textarea = await self.page.wait_for_selector(
                'textarea[placeholder="Сообщение для DeepSeek"]',
                timeout=15000
            )
            await textarea.click()
            await textarea.fill(message)
            print(f"[SEND] Message: {message[:50]}...")

            await asyncio.sleep(1)

            send_svg = await self.page.wait_for_selector(
                'svg[viewBox="0 0 16 16"] path[d*="8.3125 0.981587"]',
                timeout=15000
            )
            parent_div = await send_svg.evaluate_handle(
                "el => el.closest('div[role=\"button\"]') || el.parentElement.parentElement"
            )
            await parent_div.click()
            print("[SEND] Send button clicked")

            return True

        except PlaywrightTimeoutError as e:
            print(f"[SEND] Error: {e}")
            return False

    async def get_all_messages(self):
        try:
            messages = []
            for i in range(2, 20, 2):
                text = await self.get_message_by_id(i)
                if text:
                    messages.append({'id': i, 'text': text})
            return messages
        except Exception as e:
            print(f"[ERR] Get all messages: {e}")
            return []

    async def get_message_by_id(self, message_id: int) -> str:
        try:
            message_element = await self.page.query_selector(
                f'div[data-virtual-list-item-key="{message_id}"]'
            )

            if not message_element:
                return ""

            text_elements = await message_element.query_selector_all(
                '.ds-markdown-paragraph span, .ds-collapsible-text span'
            )

            message_text = ""
            for elem in text_elements:
                text = await elem.text_content()
                if text:
                    message_text += text

            return message_text.strip()

        except Exception as e:
            return ""

    async def get_response(self, start_message_id: int) -> str:
        response = ""
        checked_id = start_message_id
        max_checks = 0
        last_text = ""
        found_end = False
        
        print(f"[WAIT] Checking messages from ID {checked_id}")
        
        while max_checks < 60:
            message_text = await self.get_message_by_id(checked_id)
            
            if message_text and message_text != last_text:
                last_text = message_text
                print(f"[RECV] Message {checked_id} (length: {len(message_text)})")
                
                if "END_OF_MESSAGE" in message_text:
                    response = message_text.replace("END_OF_MESSAGE", "").strip()
                    print(f"[RECV] Full response received from message {checked_id}")
                    return response
                
                checked_id += 2
            
            await asyncio.sleep(2)
            max_checks += 1
            
            if max_checks % 10 == 0:
                print(f"[WAIT] Still waiting... ({max_checks*2}s elapsed)")
        
        print("[ERR] Timeout waiting for response")
        return response

    async def interactive_mode(self):
        print("\n" + "="*60)
        print("dOpenAgent")
        print(f"[CWD] Current directory: {self.current_dir}")
        print(f"[CFG] Config dir: {self.config_dir}")
        print("="*60)

        system_prompt = """Ты профессиональный ассистент с доступом к выполнению команд в системе PowerShell.

ПРАВИЛА ОТВЕТОВ:
1. НЕ используй MarkDown в ответах
2. Отвечай простым текстом без форматирования
3. Для выполнения команд используй специальные теги:

<command>команда powershell</command> - для быстрых команд

<write_file src="путь/к/файлу.py">
содержимое файла
</write_file> - для создания файлов

<run logs="путь/к/логам.txt">
длительная команда
</run> - для длительных процессов

ВАЖНО: В конце каждого ответа добавляй END_OF_MESSAGE"""

        try:
            with open(os.path.join(self.current_dir, "system_prompt.txt"), "w", encoding="utf-8") as f:
                f.write(system_prompt)
            print("[LOAD] System prompt created")
        except:
            pass

        prompt_files = ["system_prompt.txt", "system_promt.txt"]
        prompt_content = None
        
        for filename in prompt_files:
            path = os.path.join(self.current_dir, filename)
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    prompt_content = f.read().strip()
                print(f"[LOAD] System prompt from {filename}")
                break
        
        if not prompt_content:
            print("[ERR] system_prompt.txt not found")
            return
            
        system_prompt = prompt_content

        print("[INIT] Sending system prompt...")
        if not await self.send_message(system_prompt):
            print("[ERR] Failed to send system prompt")
            return

        print("[INIT] Waiting for system prompt response...")
        response = await self.get_response(2)
        if response:
            print(f"\n[SYSTEM RESPONSE]\n{response}\n")
        else:
            print("[ERR] No system prompt response")
            print("[INFO] Checking all existing messages...")
            messages = await self.get_all_messages()
            for msg in messages:
                print(f"[MSG] ID {msg['id']}: {msg['text'][:100]}...")

        while True:
            print("\n" + "-"*60)
            user_message = input("[INPUT] Your question (exit to quit): ").strip()

            if user_message.lower() in ['exit', 'quit', 'выход']:
                print("[EXIT] Shutting down...")
                break

            if not user_message:
                print("[WARN] Empty message")
                continue

            self.message_count += 1
            
            print(f"\n[SEND] Question: {user_message[:50]}...")
            if not await self.send_message(user_message):
                print("[ERR] Failed to send message")
                continue

            response_id = 2 + (self.message_count * 2)
            print(f"[WAIT] Waiting for answer #{self.message_count} (ID: {response_id})...")

            response = await self.get_response(response_id)

            if response:
                print(f"\n[RESPONSE]\n{response}\n")
                
                commands = self.parse_commands(response)
                if commands:
                    print(f"\n[CMD] Found {len(commands)} commands in response")
                    results = await self.process_commands(response)
                    
                    if results:
                        result_text = "COMMAND EXECUTION RESULTS:\n\n"
                        for i, res in enumerate(results, 1):
                            result_text += f"=== COMMAND #{i} ===\n"
                            if res['type'] == 'command':
                                result_text += f"Type: Command\n"
                                result_text += f"Input: {res['input']}\n"
                                result_text += f"Success: {res['result']['success']}\n"
                                result_text += f"Output: {res['result']['output']}\n"
                                if not res['result']['success']:
                                    result_text += f"Error Code: {res['result']['code']}\n"
                            elif res['type'] == 'write_file':
                                result_text += f"Type: Write File\n"
                                result_text += f"Path: {res['path']}\n"
                                result_text += f"Success: {res['result']['success']}\n"
                                if res['result']['success']:
                                    result_text += f"Size: {res['result']['size']} bytes\n"
                                else:
                                    result_text += f"Error: {res['result']['error']}\n"
                            elif res['type'] == 'run':
                                result_text += f"Type: Run Process\n"
                                result_text += f"Command: {res['cmd']}\n"
                                result_text += f"Log File: {res['log_file']}\n"
                                result_text += f"Success: {res['result']['success']}\n"
                                if res['result']['success']:
                                    result_text += f"PID: {res['result']['pid']}\n"
                                    result_text += f"Exit Code: {res['result']['code']}\n"
                                else:
                                    result_text += f"Error: {res['result'].get('error', 'Unknown error')}\n"
                            result_text += "\n"
                        
                        print(f"\n[SEND] Sending execution results to AI...")
                        await self.send_message(result_text + "\nEND_OF_MESSAGE")
                        
                        self.message_count += 1
                        response_id = 2 + (self.message_count * 2)
                        print(f"[WAIT] Waiting for AI response...")
                        ai_response = await self.get_response(response_id)
                        if ai_response:
                            print(f"\n[AI RESPONSE]\n{ai_response}\n")
            else:
                print("[ERR] No response")

    async def run(self):
        try:
            await self.init_browser()

            if not await self.login():
                print("[ERR] Login failed")
                return

            await self.interactive_mode()

        except Exception as e:
            print(f"[ERR] Critical: {e}")
        finally:
            if self.browser:
                await self.browser.close()
                print("[OK] Browser closed")


async def main():
    print("="*60)
    print("dOpenAgent")
    print("="*60)

    parser = DeepSeekParser()
    
    if parser.load_creds():
        print(f"[AUTH] Creds loaded from {parser.creds_file}")
        print(f"[AUTH] Email: {parser.email}")
    else:
        print("[AUTH] No saved credentials found")
        email = input("[LOGIN] Email: ").strip()
        password = input("[LOGIN] Password: ").strip()

        if not email or not password:
            print("[ERR] Email and password required")
            return

        parser.email = email
        parser.password = password
        parser.save_creds(email, password)

    await parser.run()


if __name__ == "__main__":
    asyncio.run(main())