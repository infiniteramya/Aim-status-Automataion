import time
import re
import pandas as pd
import pytesseract
import cv2

from playwright.sync_api import sync_playwright, TimeoutError

LOCAL_CSV = "a.csv"

LOGIN_URL = "https://aimapp2.aim.gov.in/atl_tranche_document2019/index.php"
ATL_DOC_URL = "https://aimapp2.aim.gov.in/atl_tranche_document2019/ATL_document.php"

APPROVED_KEYWORDS = [
    "submitted successfully",
    "reviewed with approval",
    "view and print your submitted application"
]

ERROR_KEYWORDS = ["please", "required", "mandatory"]

MAX_STEP_WAIT = 120

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

def solve_captcha(image_path):
    img = cv2.imread(image_path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=1.8, fy=1.8, interpolation=cv2.INTER_LINEAR)
    _, thresh = cv2.threshold(gray, 120, 255, cv2.THRESH_BINARY)

    config = r"--oem 3 --psm 7 -c tessedit_char_whitelist=abcdefghijklmnopqrstuvwxyz0123456789"
    text = pytesseract.image_to_string(thresh, config=config)
    return re.sub(r"[^a-zA-Z0-9]", "", text)

def read_sheet():
    try:
        df = pd.read_csv(LOCAL_CSV)
    except:
        df = pd.read_csv(LOCAL_CSV, sep="\t")

    df.columns = df.columns.str.strip()

    df.rename(columns={
        "Email ID": "email",
        "ATL Code": "atl"
    }, inplace=True)

    required_inputs = ["email", "atl"]
    for col in required_inputs:
        if col not in df.columns:
            raise Exception(f"Missing required column in CSV: {col}")

    if "status" not in df.columns:
        df["status"] = ""
    if "error url" not in df.columns:
        df["error url"] = ""
    if "error message" not in df.columns:
        df["error message"] = ""

    ordered_cols = []
    for col in ["Sr No", "Name of the school", "atl", "email", "status", "error url", "error message"]:
        if col in df.columns:
            ordered_cols.append(col)

    df = df[ordered_cols]

    print("COLUMNS FOUND:", df.columns.tolist())
    return df

def extract_error_messages(page):
    text = page.inner_text("body")
    errors = []

    for line in text.splitlines():
        clean = line.strip()
        if clean and any(k in clean.lower() for k in ERROR_KEYWORDS):
            errors.append(clean)

    return list(dict.fromkeys(errors))

def logout_if_possible(page):
    try:
        if page.locator("text=Logout").count() > 0:
            page.locator("text=Logout").first.click()
            page.wait_for_timeout(5000)
    except:
        pass

def auto_login(page, email, atl_code):
    print(f"\n▶ Logging in: {email} | {atl_code}")

    for attempt in range(10):
        print(f"   Login attempt {attempt + 1}/10")

        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        except:
            print("   ⚠ Connection reset… retrying")
            time.sleep(8)
            continue

        page.fill("#m__Email", email)
        page.locator(
            "input[name='uniqueCode'], input[id*='Unique'], input[id*='Code']"
        ).first.fill(str(atl_code))

        page.select_option("select", label="Tranche II Portal")

        captcha_img = page.locator("#m__Captcha").locator("xpath=following::img[1]")
        captcha_img.screenshot(path="captcha.png")

        captcha_text = solve_captcha("captcha.png")
        print("   OCR:", captcha_text)

        if len(captcha_text) < 4:
            print("   ❌ Weak OCR, retrying")
            time.sleep(2)
            continue

        page.fill("#m__Captcha", captcha_text)
        page.locator("input[type='submit']").click(force=True)

        try:
            page.wait_for_load_state("networkidle", timeout=25000)
        except TimeoutError:
            pass

        if not page.locator("text=Invalid Captcha").is_visible():
            print("   ✅ Login success")
            return True

        print("   ❌ Login failed, retrying")
        time.sleep(2)

    print(f"   ❌ Login failed after 10 attempts: {email}")
    return False

def process_atl_documents(page):
    page.goto(ATL_DOC_URL, timeout=1200000)
    print("   ▶ ATL document flow started")

    last_url_before_click = None

    while True:
        step_start = time.time()
        body_text = page.inner_text("body").lower()

        if any(k in body_text for k in APPROVED_KEYWORDS):
            print("   ✅ RESULT → APPROVED")
            return "APPROVED", "", ""

        if last_url_before_click == page.url and any(k in body_text for k in ERROR_KEYWORDS):
            errors = extract_error_messages(page)
            return "NOT APPROVED", page.url, " | ".join(errors)

        submit_btn = page.locator(
            "xpath=//button[contains(text(),'Submit')] | "
            "//input[( @type='submit' or @type='button') and contains(@value,'Submit')]"
        )
        save_btn = page.locator(
            "xpath=//button[contains(text(),'Save')] | "
            "//input[( @type='submit' or @type='button') and contains(@value,'Save')]"
        )

        if submit_btn.count() == 0 and save_btn.count() == 0:
            if time.time() - step_start > MAX_STEP_WAIT:
                return "NOT APPROVED", page.url, ""
            time.sleep(2)
            continue

        last_url_before_click = page.url

        if submit_btn.count() > 0:
            print("   Clicking SUBMIT")
            submit_btn.first.click()
        else:
            print("   Clicking SAVE")
            save_btn.first.click()

        time.sleep(3)


def main():
    df = read_sheet()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            slow_mo=50,
            args=["--disable-blink-features=AutomationControlled"]
        )

        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()
        page.set_default_timeout(120000)

        for idx, row in df.iloc[0:1].iterrows():
            email = row["email"]
            atl_code = row["atl"]

            success = auto_login(page, email, atl_code)

            if not success:
                df.at[idx, "status"] = "INVALID CREDENTIALS"
                df.at[idx, "error url"] = ""
                df.at[idx, "error message"] = ""
                df.to_csv(LOCAL_CSV, index=False)
                continue

            status, url, message = process_atl_documents(page)

            df.at[idx, "status"] = status
            df.at[idx, "error url"] = url
            df.at[idx, "error message"] = message
            df.to_csv(LOCAL_CSV, index=False)

            logout_if_possible(page)

        browser.close()


if __name__ == "__main__":
    main()