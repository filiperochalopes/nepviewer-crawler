import logging
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

# Selectors flexiveis caso o layout mude
LOGIN_MARKER_SELECTORS = [
    "#form_item_account",
    "#form_item_username",
    "#form_item_email",
    "input[type='email']",
    "input[name*='email' i]",
    "input[id*='email' i]",
    "input[name*='account' i]",
    "input[id*='account' i]",
    "input[placeholder*='email' i]",
    "input[placeholder*='e-mail' i]",
    "input[type='password']",
]
EMAIL_INPUT_SELECTORS = [
    "#form_item_account",
    "#form_item_username",
    "#form_item_email",
    "input[type='email']",
    "input[name*='email' i]",
    "input[id*='email' i]",
    "input[placeholder*='email' i]",
    "input[placeholder*='e-mail' i]",
    "input[name*='account' i]",
    "input[id*='account' i]",
]
PASS_INPUT_SELECTORS = [
    "#form_item_password",
    "input[type='password']",
    "input[name*='password' i]",
    "input[id*='password' i]",
]
SUBMIT_SELECTORS = [
    "button[type='submit']",
    "button:has-text('Login')",
    "button:has-text('Entrar')",
    "button:has-text('Sign in')",
]

# XPath robusto (value dentro do box principal)
POWER_XPATH = (
    "(//div[contains(concat(' ',normalize-space(@class),' '),' main-box ')]"
    "//div[contains(concat(' ',normalize-space(@class),' '),' statistics-box ')]"
    "//div[contains(concat(' ',normalize-space(@class),' '),' static-item ')])[1]"
    "//div[contains(concat(' ',normalize-space(@class),' '),' item-2 ')"
    " and .//div[contains(concat(' ',normalize-space(@class),' '),' value ')]][1]"
    "//div[contains(concat(' ',normalize-space(@class),' '),' value ')]"
)

# Intervalo de coleta (segundos)
INTERVAL_SECONDS = int(os.environ.get("INTERVAL_SECONDS", "60"))

# Reinicia o browser a cada N coletas (ex.: 360 = 6h se 1/min)
RESTART_EVERY_N_RUNS = int(os.environ.get("RESTART_EVERY_N_RUNS", "360"))

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


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
    logger.info("power_w=%s ts_local=%s", power_w, ts_local)


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

    def _first_visible(self, selectors):
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                if loc.is_visible():
                    return loc, sel
            except Exception:
                continue
        return None, None

    def _looks_like_login(self) -> bool:
        url = self.page.url or ""
        if "redirect=" in url or "login" in url:
            return True
        for sel in LOGIN_MARKER_SELECTORS:
            try:
                if self.page.locator(sel).first.is_visible():
                    return True
            except Exception:
                continue
        return False

    def _attempt_login(self) -> bool:
        email_loc, email_sel = self._first_visible(EMAIL_INPUT_SELECTORS)
        pass_loc, pass_sel = self._first_visible(PASS_INPUT_SELECTORS)
        if not email_loc or not pass_loc:
            logger.warning(
                "Nao encontrei campos de login visiveis (email_sel=%s pass_sel=%s url=%s)",
                email_sel,
                pass_sel,
                self.page.url,
            )
            return False

        email_loc.fill(self.email)
        pass_loc.fill(self.password)

        submit_loc, submit_sel = self._first_visible(SUBMIT_SELECTORS)
        if submit_loc:
            submit_loc.click()
        else:
            pass_loc.press("Enter")
            submit_sel = "Enter"

        logger.info(
            "Login enviado (email_sel=%s pass_sel=%s submit_sel=%s)",
            email_sel,
            pass_sel,
            submit_sel,
        )
        return True

    def ensure_logged_in(self) -> bool:
        # Tenta ir direto para o dashboard
        try:
            if not self.page.url.startswith(DASHBOARD_URL):
                self.page.goto(DASHBOARD_URL, wait_until="domcontentloaded")
        except Exception as e:
            logger.warning("Goto failed: %s", e)

        # Espera um pouco para ver onde caímos (Dashboard ou Login)
        # Se cair no login, geralmente aparece #form_item_account
        # Se cair no dashboard, aparece .head-bar h2 contendo 'Dashboard' ou 'Potência(W)'
        
        # Estratégia: Espera por UM dos dois marcadores
        try:
            # Race condition: espera login OU dashboard
            # Selector do login: #form_item_account
            # Selector do dashboard: div.head-bar
            markers = ", ".join(LOGIN_MARKER_SELECTORS + [".head-bar"])
            self.page.wait_for_selector(markers, timeout=20_000)
        except Exception:
            logger.warning("Nao detectou nem Login nem Dashboard em %s", self.page.url)

        # Se parecer login, faz login
        if self._looks_like_login():
            logger.info("Detectada tela de login. Autenticando...")
            if not self._attempt_login():
                return False
            # Espera navegar após login
            try:
                self.page.wait_for_url("**/dashboard", timeout=60_000)
                self.page.wait_for_selector(".head-bar", timeout=30_000)
                # Salva cookies
                self.context.storage_state(path=STATE_PATH)
            except Exception as e:
                logger.warning("Falha ao esperar dashboard pos-login: %s (url=%s)", e, self.page.url)
                return False
        elif self.page.url.startswith(DASHBOARD_URL):
            # Já estamos no dashboard; força refresh para garantir dados atuais
            try:
                self.page.reload(wait_until="networkidle")
            except Exception as e:
                logger.warning("Falha ao recarregar dashboard: %s", e)

        if not self.page.url.startswith(DASHBOARD_URL):
            try:
                self.page.goto(DASHBOARD_URL, wait_until="domcontentloaded")
                markers = ", ".join(LOGIN_MARKER_SELECTORS + [".head-bar"])
                self.page.wait_for_selector(markers, timeout=20_000)
            except Exception as e:
                logger.warning("Nao foi possivel carregar dashboard: %s (url=%s)", e, self.page.url)

            if self._looks_like_login():
                logger.info("Detectada tela de login apos redirect. Autenticando...")
                if not self._attempt_login():
                    return False
                try:
                    self.page.wait_for_url("**/dashboard", timeout=60_000)
                    self.page.wait_for_selector(".head-bar", timeout=30_000)
                    self.context.storage_state(path=STATE_PATH)
                except Exception as e:
                    logger.warning("Falha ao esperar dashboard pos-login: %s (url=%s)", e, self.page.url)
                    return False

        if not self.page.locator(".head-bar").is_visible():
            logger.warning("Dashboard nao confirmado (sem .head-bar visivel). url=%s", self.page.url)
            return False

        return True

    def read_power(self) -> float:
        logger.debug("Iniciando leitura. URL: %s | Titulo: %s", self.page.url, self.page.title())

        # Verifica se estamos na URL certa
        if not self.page.url.startswith(DASHBOARD_URL):
             logger.warning("Parece que nao estamos no dashboard. URL atual: %s", self.page.url)

        # Se caiu no login por redirecionamento, tenta autenticar novamente
        if self._looks_like_login() or not self.page.url.startswith(DASHBOARD_URL):
            logger.warning("Detectada tela de login durante leitura. Reautenticando...")
            if not self.ensure_logged_in():
                raise PlaywrightTimeoutError("Não foi possível confirmar o dashboard para leitura.")

        el = self.page.locator(f"xpath={POWER_XPATH}").first
        try:
            # Tenta rápido primeiro
            el.wait_for(state="visible", timeout=20_000)
            txt = el.inner_text().strip()
            return parse_float(txt)
        except Exception:
            logger.debug("XPath especifico falhou ou timeout. Tentando varredura GLOBAL de labels...")
            
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
                        logger.debug(
                            "Encontrados %s labels no frame '%s' (%s)",
                            len(fl),
                            frame.name,
                            frame.url,
                        )
                        all_labels.extend([(l, f"frame:{frame.name}") for l in fl])
                except:
                    pass

            logger.debug("Total de labels encontrados (frames somados): %s", len(all_labels))

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
                        
                    logger.debug(
                        "[Global Item %s | %s] Label='%s' | Value='%s'",
                        i,
                        origin,
                        lbl_txt,
                        val_txt,
                    )
                    
                    # Checagem mais estrita
                    if ("Potência" in lbl_txt or "Power(W)" in lbl_txt or ("Power" in lbl_txt and "(W)" in lbl_txt)) and "kWh" not in lbl_txt:
                         if val_txt and val_txt != "N/A":
                            return parse_float(val_txt)
                except Exception as e:
                    logger.debug("[Global Item %s] Ignorado (erro leitura): %s", i, e)
            
            # Se chegou aqui, realmente não achou
            # Dump do HTML para debug profundo
            try:
                with open("/data/debug_page.html", "w", encoding="utf-8") as f:
                    f.write(self.page.content())
                logger.debug("HTML completo salvo em /data/debug_page.html")
            except Exception as e:
                logger.warning("Falha ao salvar debug html: %s", e)

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
            if not self.ensure_logged_in():
                raise PlaywrightTimeoutError("Login não confirmado; pulando leitura.")
            power_w = self.read_power()
            save_reading(power_w)
        except PlaywrightTimeoutError as e:
            logger.warning("timeout: %s", e)
            self.stop()
        except Exception as e:
            logger.warning("error: %s", e)
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

    logger.info("Running every %ss. Ctrl+C to stop.", INTERVAL_SECONDS)
    try:
        sched.start()
    finally:
        runner.stop()


if __name__ == "__main__":
    main()
