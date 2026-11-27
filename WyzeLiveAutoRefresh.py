# =============================================================================
# Script Name: WyzeLiveAutoRefresh.py
# Purpose: Detect offline Wyze cameras and frozen on-picture timestamps.
# All adjustable settings are in WyzeLiveAutoRefresh.json beside this script.
# Python 3.8+, Selenium 4, OpenCV, numpy, pytesseract
# =============================================================================
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException, WebDriverException

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / 'WyzeLiveAutoRefresh.json'


def load_config():
    try:
        with CONFIG_PATH.open('r', encoding='utf-8') as f:
            cfg = json.load(f)
    except FileNotFoundError:
        raise SystemExit('Configuration file not found: {}'.format(CONFIG_PATH))
    except json.JSONDecodeError as exc:
        raise SystemExit('Invalid JSON configuration: {}'.format(exc))
    required = ('url', 'check_seconds', 'frozen_seconds', 'recovery_seconds', 'status_seconds',
                'tile_retry_seconds', 'window', 'chrome', 'tesseract_path',
                'grid', 'error_phrases', 'ocr', 'html', 'logs',
                'auth_callback_fragment')
    missing = [key for key in required if key not in cfg]
    if missing:
        raise SystemExit('Missing configuration keys: {}'.format(', '.join(missing)))
    cfg['grid'].setdefault('names', [])
    cfg['grid'].setdefault('camera_card_selector', '')
    cfg['grid'].setdefault('camera_name_selector', '')
    cfg.setdefault('authentication', {})
    auth = cfg['authentication']
    auth.setdefault('portal_login_text', 'Log in Wyze Web Portal')
    auth.setdefault('email', '')
    auth.setdefault('password', '')
    auth.setdefault('login_url_fragment', 'auth.wyze.com/login')
    auth.setdefault('max_attempts', 2)
    auth.setdefault('login_timeout_seconds', 45)
    auth.setdefault('restart_delay_seconds', 10)
    auth.setdefault('retry_cooldown_seconds', 300)
    auth.setdefault('email_selector', 'input[type="email"]')
    auth.setdefault('password_selector', 'input[type="password"]')
    auth.setdefault('submit_selector', 'button[type="submit"]')
    pixels = cfg.setdefault('timestamp_pixels', {})
    defaults = {'crop_top': 0.90,
                'crop_left': 0.55,
                'crop_bottom': 1.0, 'crop_right': 1.0,
                'pixel_threshold': 15, 'min_changed_fraction': 0.001,
                'bright_threshold': 180, 'bright_only': False,
                'save_debug_crops': False}
    for key, value in defaults.items():
        pixels.setdefault(key, value)
    if not (0 <= pixels['crop_top'] < pixels['crop_bottom'] <= 1 and
            0 <= pixels['crop_left'] < pixels['crop_right'] <= 1):
        raise SystemExit('Invalid timestamp crop coordinates')
    return cfg


CFG = load_config()
logging.basicConfig(filename=str(BASE_DIR / CFG['logs']['monitor']),
                    level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')
pytesseract.pytesseract.tesseract_cmd = CFG['tesseract_path']
ERROR_PHRASES = tuple(CFG['error_phrases'])


def log(message):
    print('[{}] {}'.format(datetime.now().strftime('%H:%M:%S'), message), flush=True)
    logging.info(message)


def append_log(name, message):
    with (BASE_DIR / name).open('a', encoding='utf-8') as f:
        f.write(message + '\n')


def create_browser():
    options = Options()
    options.add_argument('--log-level={}'.format(CFG['chrome']['log_level']))
    options.add_argument('--user-data-dir={}'.format(CFG['chrome']['profile_path']))
    options.add_argument('--profile-directory={}'.format(CFG['chrome']['profile_directory']))
    options.set_capability('goog:loggingPrefs', {'browser': 'ALL'})
    browser = webdriver.Chrome(options=options)
    browser.set_window_size(CFG['window']['width'], CFG['window']['height'])
    browser.set_page_load_timeout(CFG['window']['page_load_timeout'])
    browser.get(CFG['url'])
    return browser


def portal_login_button(browser):
    """Find the visible portal login control by its displayed text."""
    expected = ' '.join(CFG['authentication']['portal_login_text'].split()).casefold()
    for element in browser.find_elements(By.CSS_SELECTOR, 'a, button, [role="button"], [role="link"]'):
        try:
            if (element.is_displayed() and element.is_enabled() and
                    ' '.join(element.text.split()).casefold() == expected):
                return element
        except WebDriverException:
            continue
    return None


def auth_page_state(browser):
    """Classify the visible Wyze page without OCR or reliance on a single URL."""
    auth = CFG['authentication']
    url = browser.current_url.lower()
    if auth['login_url_fragment'].lower() in url or 'auth.wyze.com/login' in url:
        return 'login', url
    try:
        fields = browser.find_elements(By.CSS_SELECTOR, auth['password_selector'])
        if any(el.is_displayed() for el in fields):
            return 'login', url
        if portal_login_button(browser) is not None:
            return 'portal', url
        body = browser.find_element(By.TAG_NAME, 'body').text.lower()[:3000]
        if 'welcome back, log in' in body or ('forgot password?' in body and 'log in' in body):
            return 'login', url
    except WebDriverException:
        pass
    if 'auth.wyze.com' in url:
        return 'auth_pending', url
    return 'other', url


def on_login_page(browser):
    return auth_page_state(browser)[0] == 'login'


def login_if_needed(browser):
    """Fill the *visible* Wyze form, using Tab to move between fields."""
    auth = CFG['authentication']
    if auth_page_state(browser)[0] == 'portal':
        try:
            log('PORTAL DETECTED: clicking Log in Wyze Web Portal')
            button = WebDriverWait(browser, 10).until(portal_login_button)
            button.click()
            WebDriverWait(browser, auth['login_timeout_seconds']).until(
                lambda d: on_login_page(d) or (
                    auth_page_state(d)[0] == 'other' and
                    '/home' in d.current_url.lower()))
            log('PORTAL LOGIN: navigation completed')
        except (TimeoutException, WebDriverException) as exc:
            log('PORTAL LOGIN ERROR: {}'.format(type(exc).__name__))
            return False
    if not on_login_page(browser):
        return auth_page_state(browser)[0] == 'other'
    if not auth['email'] or not auth['password']:
        log('LOGIN ERROR: Add authentication.email and authentication.password to JSON')
        return False
    try:
        log('LOGIN FORM: waiting for visible email and password inputs')
        def visible_field(driver, selector):
            return next((e for e in driver.find_elements(By.CSS_SELECTOR, selector)
                         if e.is_displayed() and e.is_enabled()), False)

        email = WebDriverWait(browser, 20).until(
            lambda d: visible_field(d, auth['email_selector']) or
            visible_field(d, 'input[autocomplete="username"], input[name="email"], input[placeholder*="Email"]'))
        password = WebDriverWait(browser, 20).until(
            lambda d: visible_field(d, auth['password_selector']))

        # Click email, type it, press Tab, then type password in the focused field.
        # If the website's tab order changes, fall back to the known password element.
        email.click()
        email.send_keys(Keys.CONTROL, 'a')
        email.send_keys(auth['email'])
        log('LOGIN FORM: email entered')
        email.send_keys(Keys.TAB)
        focused_is_password = browser.execute_script(
            'return document.activeElement === arguments[0]', password)
        if focused_is_password:
            password.send_keys(auth['password'])
        else:
            log('LOGIN FORM: Tab did not focus password; selecting password directly')
            password.click()
            password.send_keys(Keys.CONTROL, 'a')
            password.send_keys(auth['password'])
        log('LOGIN FORM: password entered (value hidden)')

        submit = WebDriverWait(browser, 10).until(
            lambda d: visible_field(d, auth['submit_selector']) or
            visible_field(d, 'button:not([disabled])'))
        if not submit.is_enabled():
            log('LOGIN ERROR: submit button is disabled')
            return False
        submit.click()
        log('LOGIN FORM: submitted; waiting for redirect')
        WebDriverWait(browser, auth['login_timeout_seconds']).until(
            lambda d: not on_login_page(d))
        log('LOGIN FORM: left login page; awaiting camera dashboard')
        return True
    except (TimeoutException, WebDriverException) as exc:
        log('LOGIN ERROR: {}: {}'.format(type(exc).__name__, str(exc)[:180]))
        return False


def restart_browser(browser, watchdog, reason):
    log('Restarting Chrome: {}'.format(reason))
    append_log(CFG['logs']['refresh'], '{} | Browser restart: {}'.format(
        datetime.now().strftime('%d/%m/%Y %H:%M:%S'), reason))
    if browser is not None:
        try:
            browser.quit()
        except Exception:
            pass
    time.sleep(CFG['authentication']['restart_delay_seconds'])
    browser = create_browser()
    wait_for_page(browser)
    watchdog.reset()
    return browser


def wait_for_page(browser):
    WebDriverWait(browser, CFG['window']['ready_timeout']).until(
        lambda d: d.execute_script('return document.readyState') == 'complete')



POSITION_NAMES = ("Top Left", "Top Center", "Top Right",
                  "Middle Left", "Middle Center", "Middle Right",
                  "Bottom Left", "Bottom Center", "Bottom Right")


def get_tiles(browser, screenshot):
    """Use fixed grid slots; prefer actual media bounds when available."""
    h, w = screenshot.shape[:2]
    g = CFG['grid']
    result = browser.execute_script(r"""
      const g=arguments[0], nodes=[];
      function walk(root) {
        for(const e of root.querySelectorAll('*')) {
          nodes.push(e); if(e.shadowRoot) walk(e.shadowRoot);
        }
      }
      walk(document);
      function visible(e) {
        const r=e.getBoundingClientRect(), s=getComputedStyle(e);
        return r.width>=100 && r.height>=70 && s.display!=='none' &&
          s.visibility!=='hidden' && r.right>0 && r.bottom>0 &&
          r.left<innerWidth && r.top<innerHeight;
      }
      const cards=g.camera_card_selector ? nodes.filter(e=>e.matches(g.camera_card_selector)&&visible(e)) : [];
      const players=nodes.filter(e=>e.matches('video,canvas')&&visible(e));
      return {width:innerWidth,height:innerHeight,tiles:(cards.length?cards:players).map(e=>{
        const m=e.querySelector('video,canvas');
        const r=(m&&visible(m)?m:e).getBoundingClientRect();
        return {x:r.left,y:r.top,w:r.width,h:r.height};
      })};
    """, g)
    sx, sy = w/result['width'], h/result['height']
    left,right,top,bottom = g['left']*w,g['right']*w,g['top']*h,g['bottom']*h
    gx,gy=g['gap_x']*w,g['gap_y']*h
    cw,ch=(right-left-2*gx)/3,(bottom-top-2*gy)/3
    if cw<=0 or ch<=0:
        raise ValueError('Invalid 3x3 grid geometry')
    slots={}
    for item in result['tiles']:
        x,y,ww,hh=item['x']*sx,item['y']*sy,item['w']*sx,item['h']*sy
        cx,cy=x+ww/2,y+hh/2
        if not (left<=cx<right and top<=cy<bottom): continue
        col=min(2,int((cx-left)/(cw+gx)))
        row=min(2,int((cy-top)/(ch+gy)))
        idx=row*3+col
        rect=(max(0,int(x)),max(0,int(y)),min(w,int(x+ww)),min(h,int(y+hh)))
        if rect[2]<=rect[0] or rect[3]<=rect[1]: continue
        score=abs(cx-(left+col*(cw+gx)+cw/2))/cw+abs(cy-(top+row*(ch+gy)+ch/2))/ch
        if idx not in slots or score<slots[idx][0]: slots[idx]=(score,rect)
    return [(name,*(slots[i][1] if i in slots else (0,0,0,0)))
            for i,name in enumerate(POSITION_NAMES)]


def normalize(text):
    return re.sub(r'\s+', ' ', text or '').lower().strip()


def detect_html_errors(browser):
    """Read visible DOM and open shadow roots; return a list of matched errors.

    Avoid matching hidden text by checking element geometry and computed style.
    """
    return browser.execute_script(r'''
      const phrases = arguments[0];
      const matches = [], seen = new Set();
      function walk(root) {
        for (const el of root.querySelectorAll('*')) {
          if (el.shadowRoot) walk(el.shadowRoot);
          if (el.children.length || !el.textContent) continue;
          const r = el.getBoundingClientRect();
          const s = getComputedStyle(el);
          if (!r.width || !r.height || s.visibility === 'hidden' || s.display === 'none') continue;
          const txt = el.textContent.toLowerCase().replace(/\s+/g,' ').trim();
          const phrase = phrases.find(p => txt.includes(p));
          if (phrase) {
            const key = phrase + ':' + Math.round(r.x/20) + ':' + Math.round(r.y/20);
            if (!seen.has(key)) { seen.add(key); matches.push({phrase, x:r.x, y:r.y}); }
          }
        }
      }
      walk(document);
      return matches;
    ''', list(ERROR_PHRASES))


def click_tile_refresh(browser, point):
    """Click the nearest visible Refresh button inside the error's camera card.

    Never click a global browser/page refresh button by accident.
    """
    return browser.execute_script(r'''
      const pt = arguments[0];
      const settings = arguments[1];
      const candidates = [];
      function walk(root) {
        for (const el of root.querySelectorAll('*')) {
          if (el.shadowRoot) walk(el.shadowRoot);
          const t = (el.innerText || el.textContent || '').trim();
          if (t !== settings.refresh_button_text) continue;
          const r = el.getBoundingClientRect();
          if (r.width < settings.min_button_width || r.height < settings.min_button_height) continue;
          const s = getComputedStyle(el);
          if (s.display === 'none' || s.visibility === 'hidden') continue;
          const button = el.closest('button,[role="button"]') || el;
          const b = button.getBoundingClientRect();
          const distance = Math.hypot((b.x+b.width/2)-pt.x, (b.y+b.height/2)-pt.y);
          if (distance < settings.refresh_max_distance) candidates.push({button, distance});
        }
      }
      walk(document);
      candidates.sort((a,b) => a.distance-b.distance);
      if (!candidates.length) return false;
      candidates[0].button.click();
      return true;
    ''', point, CFG['html'])


def ocr_tile_errors(browser, screenshot, tiles):
    """OCR each tile; return its name and a match when an offline error is visible."""
    errors = []
    for name, x1, y1, x2, y2 in tiles:
        tile = screenshot[y1:y2, x1:x2]
        if tile.size == 0:
            continue
        gray = cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=CFG['ocr']['tile_scale'], fy=CFG['ocr']['tile_scale'], interpolation=cv2.INTER_CUBIC)
        match = None
        for variant in (gray, cv2.bitwise_not(gray)):
            for psm in CFG['ocr']['tile_psm']:
                try:
                    text = normalize(pytesseract.image_to_string(variant, config='--psm {}'.format(psm)))
                except Exception as exc:
                    log('OCR error: {}'.format(exc))
                    break
                match = next((phrase for phrase in ERROR_PHRASES if phrase.lower() in text), None)
                if match:
                    break
            if match:
                break
        if match:
            errors.append((name, match, ((x1+x2)/2, (y1+y2)/2)))
    return errors


def timestamp_image(tile, name):
    settings = CFG['timestamp_pixels']
    if tile is None or tile.size == 0:
        return None
    h, w = tile.shape[:2]
    crop = tile[int(h * settings['crop_top']):int(h * settings['crop_bottom']),
                int(w * settings['crop_left']):int(w * settings['crop_right'])]
    if crop.size == 0:
        return None
    if settings['save_debug_crops']:
        folder = BASE_DIR / 'timestamp_debug'
        folder.mkdir(exist_ok=True)
        cv2.imwrite(str(folder / (name.replace(' ', '_') + '.png')), crop)
    return cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)


class TimestampWatchdog:
    def __init__(self):
        self.states = {}
        self.last_status = 0

    def reset(self):
        self.states.clear()
        self.last_status = 0

    def check(self, browser, screenshot, errors, tiles):
        now = time.monotonic()
        report = now - self.last_status >= CFG['status_seconds']
        settings = CFG['timestamp_pixels']
        frozen = []
        if report:
            log("========== CAMERA STATUS: {} cameras ==========".format(len(tiles)))
        for name, x1, y1, x2, y2 in tiles:
            tile = screenshot[y1:y2, x1:x2]
            current = timestamp_image(tile, name) if tile.size else None
            camera_id = name
            if not tile.size:
                self.states.pop(camera_id, None)
            state = self.states.setdefault(camera_id, {'image': None, 'last_change': now})
            old = state['image']
            fraction = 0.0
            if current is not None:
                if old is None or old.shape != current.shape:
                    state['image'] = current.copy()
                    state['last_change'] = now
                else:
                    diff = cv2.absdiff(old, current)
                    fraction = np.count_nonzero(diff > settings['pixel_threshold']) / diff.size
                    if fraction >= settings['min_changed_fraction']:
                        state['last_change'] = now
                        state['image'] = current.copy()
            stalled = int(now - state['last_change'])
            if not tile.size:
                status = 'NOT VISIBLE'
            elif name in errors:
                status = 'ERROR: ' + errors[name]
            elif stalled >= CFG['frozen_seconds']:
                status = 'FROZEN'
            elif current is None:
                status = 'TIMESTAMP MISSING'
            elif old is None or old.shape != current.shape:
                status = 'INITIALIZING'
            elif fraction >= settings['min_changed_fraction']:
                status = 'ACTIVE'
            else:
                status = 'UNCHANGED - WATCHING'
            if report:
                log('{:<30} | {:<25} | unchanged={:>4}s | pixel_change={:.3f}%'.format(
                    name, status, stalled, fraction * 100))
            if tile.size and stalled >= CFG['frozen_seconds'] and name not in errors:
                frozen.append('{}: timestamp region unchanged or missing for {}s'.format(name, stalled))
        if report:
            log('===================================')
            self.last_status = now
        return '; '.join(frozen) if frozen else None


def refresh_page(browser, watchdog, reason):
    log('Refreshing entire Wyze page: {}'.format(reason))
    append_log(CFG['logs']['refresh'], '{} | {}'.format(
        datetime.now().strftime('%d/%m/%Y %H:%M:%S'), reason))
    watchdog.reset()
    try:
        browser.refresh()
        wait_for_page(browser)
        log('Page refreshed; waiting for camera streams')
    except Exception as exc:
        log('Refresh error: {}'.format(exc))
    return time.monotonic() + CFG['recovery_seconds']


def main():
    browser = None
    watchdog = TimestampWatchdog()
    # After a camera-specific refresh, allow 30 seconds before trying again.
    tile_retry_until = 0
    last_tile_issue = None
    auth_failures = 0
    auth_retry_after = 0
    last_auth_status = 0
    auth_pending_since = None
    try:
        browser = create_browser()
        wait_for_page(browser)
        log('Wyze monitor started; check={}s freeze={}s'.format(
            CFG['check_seconds'], CFG['frozen_seconds']))
        recovery_until = time.monotonic() + CFG['recovery_seconds']
        while True:
            try:
                now = time.monotonic()
                state, current_url = auth_page_state(browser)
                if now - last_auth_status >= CFG['status_seconds']:
                    log('AUTH CHECK: state={} url={}'.format(state, current_url.split('?')[0]))
                    last_auth_status = now
                if state == 'auth_pending':
                    if auth_pending_since is None:
                        auth_pending_since = now
                    if now - auth_pending_since < CFG['authentication']['login_timeout_seconds']:
                        time.sleep(CFG['check_seconds'])
                        continue
                    log('AUTH CHECK: authentication redirect timed out')
                    state = 'login'
                else:
                    auth_pending_since = None
                if state in ('login', 'portal'):
                    log('LOGIN DETECTED: Wyze authentication required ({})'.format(state))
                    if now < auth_retry_after:
                        log('AUTH COOLDOWN: waiting {}s before another attempt'.format(int(auth_retry_after - now)))
                        time.sleep(min(CFG['check_seconds'], auth_retry_after - now))
                        continue
                    # Fill the actual page shown on screen before restarting Chrome.
                    # Restart only when this attempt fails; then try once in the fresh browser.
                    logged_in = login_if_needed(browser)
                    if not logged_in and state != 'portal':
                        browser = restart_browser(browser, watchdog, 'Login attempt did not complete')
                        logged_in = login_if_needed(browser)
                    last_tile_issue = None
                    tile_retry_until = 0
                    watchdog.reset()
                    if logged_in:
                        auth_failures = 0
                        auth_retry_after = 0
                        log('LOGIN RECOVERY: continuing monitoring')
                    else:
                        auth_failures += 1
                        auth_retry_after = time.monotonic() + CFG['authentication']['retry_cooldown_seconds']
                        log('LOGIN RECOVERY: waiting for verification or cooldown')
                    recovery_until = time.monotonic() + CFG['recovery_seconds']
                    continue
                for entry in browser.get_log('browser'):
                    if entry.get('level') == 'SEVERE':
                        append_log(CFG['logs']['console'], str(entry))
                if CFG['auth_callback_fragment'] in browser.current_url:
                    recovery_until = time.monotonic() + CFG['recovery_seconds']
                elif time.monotonic() >= recovery_until:
                    png = browser.get_screenshot_as_png()
                    screenshot = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
                    if screenshot is None:
                        raise ValueError('Cannot decode screenshot')
                    html_errors = detect_html_errors(browser)
                    tiles = get_tiles(browser, screenshot)
                    ocr_errors = ocr_tile_errors(browser, screenshot, tiles)
                    camera_errors = {name: phrase for name, phrase, center in ocr_errors}
                    named_html = []
                    for err in html_errors:
                        name = next((n for n, x1, y1, x2, y2 in tiles
                                     if x1 <= err['x'] < x2 and y1 <= err['y'] < y2), None)
                        if name is not None:
                            camera_errors[name] = err['phrase']
                        named_html.append((name, err))
                    frozen_reason = watchdog.check(browser, screenshot, camera_errors, tiles)
                    if html_errors or ocr_errors:
                        if html_errors:
                            name, err = named_html[0]
                            reason = '{}: HTML: {}'.format(name or 'Unmapped camera', err['phrase'])
                            point = {'x': err['x'], 'y': err['y']}
                        else:
                            name, phrase, center = ocr_errors[0]
                            reason = '{}: {}'.format(name, phrase)
                            point = {'x': center[0], 'y': center[1]}
                        if time.monotonic() < tile_retry_until:
                            log('Waiting for camera-specific recovery: {}'.format(reason))
                        elif last_tile_issue == reason:
                            recovery_until = refresh_page(browser, watchdog,
                                                          'Camera-specific retry failed: ' + reason)
                            last_tile_issue = None
                            tile_retry_until = 0
                        elif click_tile_refresh(browser, point):
                            log('Clicked camera Refresh button: {}'.format(reason))
                            append_log(CFG['logs']['refresh'], '{} | Tile refresh: {}'.format(
                                datetime.now().strftime('%d/%m/%Y %H:%M:%S'), reason))
                            last_tile_issue = reason
                            tile_retry_until = time.monotonic() + CFG['tile_retry_seconds']
                        else:
                            recovery_until = refresh_page(browser, watchdog,
                                                          'No camera Refresh button: ' + reason)
                            last_tile_issue = None
                    else:
                        last_tile_issue = None
                        tile_retry_until = 0
                    if frozen_reason and time.monotonic() >= recovery_until:
                        recovery_until = refresh_page(browser, watchdog, frozen_reason)
                        last_tile_issue = None
                        tile_retry_until = 0
            except Exception as exc:
                log('Monitoring error: {}'.format(exc))
            time.sleep(CFG['check_seconds'])
    except KeyboardInterrupt:
        log('Stopped by user')
    finally:
        if browser is not None:
            try:
                browser.quit()
            except Exception:
                pass
        log('Wyze monitor stopped')


if __name__ == '__main__':
    main()
