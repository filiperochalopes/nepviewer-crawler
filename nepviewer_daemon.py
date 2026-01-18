import os
import re
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


DASHBOARD_URL = "https://user.nepviewer.com/dashboard"
LOGIN_URL = "https://user.nepviewer.com/"
TIMEZONE = ZoneInfo("America/Bahia")

SQLITE_PATH = os.environ.get("SQLITE_PATH", "nepviewer.db")
STATE_PATH = os.environ.get("STATE_PATH", "state.json")

# Login selectors (ids fixos)
EMAIL_SEL = "#form_item_account"
PASS_SEL = "#form_item_password"
SUBMIT_SEL = "button[type='submit']"

# XPath robusto (label -> value)
POWER_XPATH = (
    "//div[contains(@class,'item')]"
    "[.//div[contains(@class,'label') and normalize-space(.)='Potência(W)']]"
    "//div[contains(@class,'value')]"
)

# Intervalo de coleta (segundos)
INTERVAL_SECONDS = int(os.environ.get("INTERVAL_SECONDS", "60"))

# Reinicia o browser a cada N coletas (ex.: 360 = 6h se 1/min)
RESTART_EVERY_N_RUNS = int(os.environ.get("RESTART_EVERY_N_RUNS", "360"))


def init_db():
    conn = sqlite3.connect(SQLITE_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS nep_power (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_local TEXT NOT NULL,
            power_w REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def save_reading(power_w: float):
    ts_local = datetime.now(TIMEZONE).isoformat(timespec="seconds")
    conn = sqlite3.connect(SQLITE_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO nep_power (ts_local, power_w) VALUES (?, ?)", (ts_local, power_w))
    conn.commit()
    conn.close()
    print(f"[{ts_local}] power_w={power_w}")


def parse_float(text: str) -> float:
    t = text.strip()
    # pt-BR: 3.712,00
    if re.match(r"^\d{1,3}(\.\d{3})*(,\d+)?$", t):
        t = t.replace(".", "").replace(",", ".")
    return float(t)


def file_exists(path: str) -> bool:
    try:
        with open(path, "rb"):
            return True
    except FileNotFoundError:
        return False


class NepViewerRunner:
    def __init__(self, email: str, password: str, headless: bool = True):
        self.email = email
        self.password = password
        self.headless = headless

        self.pw = None
        self.browser = None
        self.context = None
        self.page = None

        self.run_count = 0

    def start(self):
        self.pw = sync_playwright().start()
        # Força locale pt-BR
        self.browser = self.pw.chromium.launch(headless=self.headless, args=["--lang=pt-BR"])

        # reaproveita sessão se existir state.json
        if file_exists(STATE_PATH):
            self.context = self.browser.new_context(storage_state=STATE_PATH, locale="pt-BR", timezone_id="America/Bahia")
        else:
            self.context = self.browser.new_context(locale="pt-BR", timezone_id="America/Bahia")

        self.page = self.context.new_page()

    def stop(self):
        try:
            if self.context:
                self.context.close()
        finally:
            try:
                if self.browser:
                    self.browser.close()
            finally:
                if self.pw:
                    self.pw.stop()

        self.pw = self.browser = self.context = self.page = None

    def ensure_logged_in(self):
        # Tenta ir direto para o dashboard
        try:
            if self.page.url != DASHBOARD_URL:
                self.page.goto(DASHBOARD_URL, wait_until="domcontentloaded")
        except Exception as e:
            print(f"[WARN] Goto failed: {e}")

        # Espera um pouco para ver onde caímos (Dashboard ou Login)
        # Se cair no login, geralmente aparece #form_item_account
        # Se cair no dashboard, aparece .head-bar h2 contendo 'Dashboard' ou 'Potência(W)'
        
        # Estratégia: Espera por UM dos dois marcadores
        try:
            # Race condition: espera login OU dashboard
            # Selector do login: #form_item_account
            # Selector do dashboard: div.head-bar
            self.page.wait_for_selector("#form_item_account, .head-bar", timeout=20_000)
        except Exception:
            print(f"[WARN] Não detectou nem Login nem Dashboard em {self.page.url}")

        # Se tiver campo de login, faz login
        if self.page.locator(EMAIL_SEL).is_visible():
            print("Detectada tela de login. Autenticando...")
            self.page.fill(EMAIL_SEL, self.email)
            self.page.fill(PASS_SEL, self.password)
            self.page.click(SUBMIT_SEL)
            
            # Espera navegar após login
            try:
                self.page.wait_for_url("**/dashboard", timeout=60_000)
                self.page.wait_for_selector(".head-bar", timeout=30_000)
                # Salva cookies
                self.context.storage_state(path=STATE_PATH)
            except Exception as e:
                print(f"[WARN] Falha ao esperar dashboard pós-login: {e}")

    def read_power(self) -> float:
        print(f"[DEBUG] Iniciando leitura. URL: {self.page.url} | Título: {self.page.title()}")

        # Verifica se estamos na URL certa
        if "dashboard" not in self.page.url:
             print(f"[WARN] Parece que não estamos no dashboard. URL atual: {self.page.url}")

        # Garante que carregou pelo menos algum label (CSS selector é melhor que XPath para Shadow DOM)
        try:
            self.page.wait_for_selector(".label", timeout=30_000)
        except PlaywrightTimeoutError:
            print(f"[ERRO] Nenhum .label encontrado na URL: {self.page.url}")
            print(f"[DEBUG] HTML Preview: {self.page.content()[:1000]}")
            raise

        # XPath mais permissivo: procura 'Potência' no label (ignora case/sulfixo exato)
        xpath = (
            "//div[contains(@class, 'item')]"
            "[.//div[contains(@class, 'label') and contains(., 'Potência')]]"
            "//div[contains(@class, 'value')]"
        )
        
        el = self.page.locator(f"xpath={xpath}").first
        try:
            # Tenta rápido primeiro
            el.wait_for(state="visible", timeout=5_000)
            txt = el.inner_text().strip()
            return parse_float(txt)
        except Exception:
            print("[DEBUG] XPath específico falhou ou timeout. Tentando varredura GLOBAL de labels...")
            
            # Tenta encontrar em todos os frames (caso use iframe)
            all_labels = []
            
            # Main frame
            main_labels = self.page.locator(".label").all()
            all_labels.extend([(l, "main") for l in main_labels])
            
            # Sub-frames
            for frame in self.page.frames:
                try:
                    fl = frame.locator(".label").all()
                    if fl:
                        print(f"[DEBUG] Encontrados {len(fl)} labels no frame '{frame.name}' ({frame.url})")
                        all_labels.extend([(l, f"frame:{frame.name}") for l in fl])
                except:
                    pass

            print(f"[DEBUG] Total de labels encontrados (frames somados): {len(all_labels)}")

            for i, (lbl_el, origin) in enumerate(all_labels):
                try:
                    lbl_txt = lbl_el.inner_text().strip()
                    
                    # Tenta achar o value no mesmo container pai
                    # Assumindo estrutura: <div> <div class="value">...</div> <div class="label">...</div> </div>
                    parent = lbl_el.locator("xpath=..")
                    val_el = parent.locator(".value").first
                    
                    val_txt = "N/A"
                    if val_el.count() > 0:
                        val_txt = val_el.inner_text().strip()
                        
                    print(f"  [Global Item {i} | {origin}] Label='{lbl_txt}' | Value='{val_txt}'")
                    
                    # Checagem mais estrita
                    if ("Potência" in lbl_txt or "Power(W)" in lbl_txt or ("Power" in lbl_txt and "(W)" in lbl_txt)) and "kWh" not in lbl_txt:
                         if val_txt and val_txt != "N/A":
                            return parse_float(val_txt)
                except Exception as e:
                    print(f"  [Global Item {i}] Ignorado (erro leitura): {e}")
            
            # Se chegou aqui, realmente não achou
            # Dump do HTML para debug profundo
            try:
                with open("/data/debug_page.html", "w", encoding="utf-8") as f:
                    f.write(self.page.content())
                print("[DEBUG] HTML completo salvo em /data/debug_page.html")
            except Exception as e:
                print(f"[WARN] Falha ao salvar debug html: {e}")

            raise PlaywrightTimeoutError("Não foi possível encontrar campo de Potência (W) após varredura global.")

    def tick(self):
        self.run_count += 1

        # Reinício periódico pra evitar leak
        if RESTART_EVERY_N_RUNS > 0 and (self.run_count % RESTART_EVERY_N_RUNS == 0):
            self.stop()

        # Garante que o browser esteja rodando (cobre 1ª execução, restart acima e crash anterior)
        if self.page is None:
            self.start()

        try:
            self.ensure_logged_in()
            power_w = self.read_power()
            save_reading(power_w)
        except PlaywrightTimeoutError as e:
            print(f"[WARN] timeout: {e}")
            self.stop()
        except Exception as e:
            print(f"[WARN] error: {e}")
            # não derruba o scheduler; tenta de novo no próximo tick, possivelmente reabrindo o browser se tiver parado
            if self.page is None:
                self.stop() # garante limpeza total se algo quebrou parcialmente


def main():
    init_db()

    email = os.environ.get("NEP_EMAIL", "").strip()
    password = os.environ.get("NEP_PASSWORD", "").strip()
    if not email or not password:
        raise SystemExit("Defina NEP_EMAIL e NEP_PASSWORD no ambiente (.env no compose).")

    headless_env = os.environ.get("HEADLESS", "true").lower() == "true"
    runner = NepViewerRunner(email, password, headless=headless_env)

    sched = BlockingScheduler(timezone=TIMEZONE)
    sched.add_job(runner.tick, "interval", seconds=INTERVAL_SECONDS, max_instances=1, coalesce=True)

    print(f"Running every {INTERVAL_SECONDS}s. Ctrl+C to stop.")
    try:
        sched.start()
    finally:
        runner.stop()


if __name__ == "__main__":
    main()
