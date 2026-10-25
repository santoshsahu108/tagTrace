#
#  GoogleFindMyTools - A set of tools to interact with the Google Find My API
#  Copyright © 2024 Leon Böttger. All rights reserved.
#
import os
import shutil
import platform
import time

try:
    import undetected_chromedriver as uc
except ImportError:  # not needed in attach mode (e.g. Termux)
    uc = None


def _is_termux():
    return "com.termux" in os.environ.get("PREFIX", "") or bool(os.environ.get("TERMUX_VERSION"))


def _attach_mode():
    """Launch the system Chromium ourselves and attach chromedriver to it.

    Used on Termux (Android), where undetected_chromedriver's desktop driver
    downloads can't run, or anywhere with CHROME_ATTACH=1. Because the browser
    is started normally (not by chromedriver), sites don't see the automation
    flag, which keeps Google's sign-in page usable.
    """
    value = os.environ.get("CHROME_ATTACH", "").strip().lower()
    if value in ("1", "true", "yes"):
        return True
    if value in ("0", "false", "no"):
        return False
    return _is_termux()


def _free_port():
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _create_attached_driver():
    import subprocess
    import tempfile
    import urllib.request

    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service

    browser = (
        os.environ.get("CHROME_BINARY", "").strip()
        or shutil.which("chromium-browser")
        or shutil.which("chromium")
        or find_chrome()
    )
    driver_path = os.environ.get("CHROMEDRIVER_PATH", "").strip() or shutil.which("chromedriver")
    if not browser:
        raise Exception("[ChromeDriver] Chromium not found. On Termux: pkg install x11-repo && pkg install chromium")
    if not driver_path:
        raise Exception("[ChromeDriver] chromedriver not found. On Termux it comes with: pkg install chromium")

    env = dict(os.environ)
    env.setdefault("DISPLAY", ":0")  # Termux:X11 default display
    port = _free_port()
    profile = tempfile.mkdtemp(prefix="tagtrace-chrome-")
    proc = subprocess.Popen(
        [
            browser,
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--start-maximized",
            "about:blank",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.time() + 60
    while True:
        if proc.poll() is not None:
            shutil.rmtree(profile, ignore_errors=True)
            raise Exception(
                "[ChromeDriver] Chromium exited at start. Is a display running? "
                "On Termux start Termux:X11 (termux-x11 :0) and set DISPLAY=:0."
            )
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1).close()
            break
        except Exception:
            if time.time() > deadline:
                proc.kill()
                shutil.rmtree(profile, ignore_errors=True)
                raise Exception("[ChromeDriver] Chromium didn't open its debugging port.")
            time.sleep(0.5)

    options = webdriver.ChromeOptions()
    options.add_experimental_option("debuggerAddress", f"127.0.0.1:{port}")
    driver = webdriver.Chrome(service=Service(driver_path), options=options)

    # quit() on an attached driver leaves the browser open; close it too.
    original_quit = driver.quit

    def quit_all():
        try:
            original_quit()
        except Exception:
            pass  # already quit (GFMT flows call quit() more than once)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(10)
                except Exception:
                    proc.kill()
            shutil.rmtree(profile, ignore_errors=True)

    driver.quit = quit_all
    print(f"[ChromeDriver] Started {os.path.basename(browser)} (attached).")
    return driver


def find_chrome():
    """Find Chrome executable using known paths and system commands."""
    possiblePaths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\ProgramData\chocolatey\bin\chrome.exe",
        r"C:\Users\%USERNAME%\AppData\Local\Google\Chrome\Application\chrome.exe",
        "/usr/bin/google-chrome",
        "/usr/local/bin/google-chrome",
        "/opt/google/chrome/chrome",
        "/snap/bin/chromium",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    ]
    # Check predefined paths
    for path in possiblePaths:
        if os.path.exists(path):
            return path
    # Use system command to find Chrome
    try:
        if platform.system() == "Windows":
            chrome_path = shutil.which("chrome")
        else:
            chrome_path = shutil.which("google-chrome") or shutil.which("chromium")
        if chrome_path:
            return chrome_path
    except Exception as e:
        print(f"[ChromeDriver] Error while searching system paths: {e}")
    return None

def get_options():
    chrome_options = uc.ChromeOptions()
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    return chrome_options

def _chrome_version_main():
    # Chromedriver must match the installed Chrome's major version. Use
    # CHROME_VERSION_MAIN if set, else ask the installed Chrome; None lets
    # undetected_chromedriver pick (latest), which fails on older Chrome.
    value = os.environ.get("CHROME_VERSION_MAIN", "").strip()
    if value.isdigit():
        return int(value)
    chrome = find_chrome()
    if not chrome:
        return None
    try:
        import re
        import subprocess

        out = subprocess.run([chrome, "--version"], capture_output=True, text=True, timeout=10).stdout
        match = re.search(r"(\d+)\.\d+", out)
        return int(match.group(1)) if match else None
    except Exception:
        return None


def create_driver():
    """Create a Chrome WebDriver with undetected_chromedriver."""
    if _attach_mode():
        return _create_attached_driver()
    try:
        # Optionally kill existing Chrome processes first. Off by default: it
        # would close the user's own browser when started from the web UI.
        try:
            if os.environ.get("CHROME_KILL_EXISTING", "").strip() != "1":
                pass
            elif platform.system() == "Windows":
                os.system("taskkill /f /im chrome.exe >nul 2>&1")
            else:
                os.system("pkill -f chrome")
            time.sleep(2)  # Wait for processes to close
        except:
            pass
            
        chrome_options = get_options()
        driver = uc.Chrome(options=chrome_options, version_main=_chrome_version_main())
        print("[ChromeDriver] Installed and browser started.")
        return driver
    except Exception as e:
        print(f"[ChromeDriver] Default ChromeDriver creation failed: {e}")
        print("[ChromeDriver] Trying alternative paths...")
        chrome_path = find_chrome()
        if chrome_path:
            chrome_options = get_options()
            chrome_options.binary_location = chrome_path
            try:
                driver = uc.Chrome(options=chrome_options, version_main=_chrome_version_main())
                print(f"[ChromeDriver] ChromeDriver started using {chrome_path}")
                return driver
            except Exception as e:
                print(f"[ChromeDriver] ChromeDriver failed using path {chrome_path}: {e}")
        else:
            print("[ChromeDriver] No Chrome executable found in known paths.")
        
        # Final fallback - try headless mode
        print("[ChromeDriver] Trying headless mode as last resort...")
        try:
            chrome_options = get_options()
            chrome_options.add_argument("--headless")
            driver = uc.Chrome(options=chrome_options, version_main=_chrome_version_main())
            print("[ChromeDriver] Started in headless mode successfully.")
            return driver
        except Exception as e:
            print(f"[ChromeDriver] Headless mode also failed: {e}")
        
        raise Exception(
            "[ChromeDriver] Failed to install ChromeDriver. A current version of Chrome was not detected on your system.\n"
            "If you know that Chrome is installed, update Chrome to the latest version. If the script is still not working, "
            "set the path to your Chrome executable manually inside the script."
        )

if __name__ == '__main__':
    create_driver()