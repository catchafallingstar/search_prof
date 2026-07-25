import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

# ==========================================
# 1. THE NEW ARCHITECTURE MOCKS
# ==========================================

# Mocking i18n.py (Thread-Local Storage)
_ctx = threading.local()

def set_radar_context(session_id, lang_code):
    _ctx.session_id = session_id
    _ctx.lang_code = lang_code

def get_radar_session():
    return getattr(_ctx, "session_id", "DEFAULT_SESSION")

def get_radar_lang():
    return getattr(_ctx, "lang_code", "en")

def t(key):
    """Mock translation pulling safely from TLS."""
    lang = get_radar_lang()
    translations = {
        "en": {"step3": "Scanning signals..."},
        "cn": {"step3": "正在扫描信号..."}
    }
    return translations.get(lang, translations["en"]).get(key, "")

# Mocking the UI Container (st.empty)
class MockUIContainer:
    def __init__(self):
        self.text = ""
        
    def code(self, text, language="shell"):
        self.text = text

# Mocking app.py (Session-Aware Router)
class SessionAwareStreamCapture:
    def __init__(self):
        self.buffers = {} 

    def register_ui(self, session_id, ui_container):
        self.buffers[session_id] = ui_container

    def write(self, text):
        if not text.strip(): return
        
        # Pulls the session ID of the thread currently trying to print
        current_session = get_radar_session()
        
        # Routes directly to that specific user's UI
        if current_session in self.buffers:
            self.buffers[current_session].text += text + "\n"

    def flush(self): pass

# Apply global router once
sys.stdout = SessionAwareStreamCapture()

# ==========================================
# 2. STEP 3 EXECUTION MOCK (Sub-Threading)
# ==========================================

def worker_wrapper(session_id, lang_code):
    """Simulates the ThreadPoolExecutor wrapper passing context down."""
    # 1. Bind context to the new sub-thread
    set_radar_context(session_id, lang_code)
    time.sleep(0.1) # Simulate work
    
    # 2. Print uses global sys.stdout -> routes to SessionAwareStreamCapture
    sys.stdout.write(f"[{get_radar_session()}] {t('step3')}")

def scan_hiring_signals_mock():
    """Simulates Step 3 firing off background threads."""
    parent_session = get_radar_session()
    parent_lang = get_radar_lang()
    
    threads = []
    # Spawning 2 concurrent background threads for THIS user
    for _ in range(2):
        th = threading.Thread(target=worker_wrapper, args=(parent_session, parent_lang))
        threads.append(th)
        th.start()
        
    for th in threads: th.join()

# ==========================================
# 3. STREAMLIT APP EXECUTION MOCK
# ==========================================

def run_streamlit_app_mock(user_name, session_id, lang_code):
    # 1. Bind User Context (Replaces os.environ)
    set_radar_context(session_id, lang_code)
    
    # 2. Register UI (Fixes Step 3 print bug)
    ui_container = MockUIContainer()
    sys.stdout.register_ui(session_id, ui_container)
    
    time.sleep(0.05) # App loading simulation
    
    # 3. Run the heavily-threaded Step 3
    scan_hiring_signals_mock()
    
    return ui_container.text

# ==========================================
# 4. THE COMPREHENSIVE TEST SUITE
# ==========================================

class TestNewArchitecture(unittest.TestCase):

    def test_concurrent_users_complete_isolation(self):
        """Proves TLS and Router securely separate users under load."""
        print("\n--- Running Architecture Validation Test ---")
        
        with ThreadPoolExecutor(max_workers=2) as executor:
            # User A (English) and User B (Chinese) hit the app at the exact same time
            future_a = executor.submit(run_streamlit_app_mock, "User A", "SESSION_A", "en")
            future_b = executor.submit(run_streamlit_app_mock, "User B", "SESSION_B", "cn")
            
            output_a = future_a.result()
            output_b = future_b.result()

        print("\n[User A's Screen (Expected: SESSION_A, English)]")
        print(output_a)
        
        print("[User B's Screen (Expected: SESSION_B, Chinese)]")
        print(output_b)

        # ASSERTION 1: Step 3 logs are actually capturing now (Not empty!)
        self.assertGreater(len(output_a), 0, "Failed: User A's UI is empty. Step 3 logs did not route.")
        self.assertGreater(len(output_b), 0, "Failed: User B's UI is empty. Step 3 logs did not route.")

        # ASSERTION 2: Session ID Isolation
        self.assertIn("SESSION_A", output_a, "Failed: User A lost their session ID.")
        self.assertNotIn("SESSION_B", output_a, "Failed: User B's session leaked into User A.")
        self.assertIn("SESSION_B", output_b, "Failed: User B lost their session ID.")
        
        # ASSERTION 3: Language Isolation
        self.assertIn("Scanning signals...", output_a, "Failed: User A lost English translation.")
        self.assertNotIn("正在扫描信号...", output_a, "Failed: User B's Chinese leaked into User A.")
        self.assertIn("正在扫描信号...", output_b, "Failed: User B lost Chinese translation.")

        print("✅ SUCCESS: All architecture tests passed! Sessions, languages, and sys.stdout are perfectly isolated.")

if __name__ == "__main__":
    unittest.main(verbosity=2)