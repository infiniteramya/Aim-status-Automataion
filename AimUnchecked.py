import time
import re
import pandas as pd
import pytesseract
import cv2
from playwright.sync_api import sync_playwright, TimeoutError

MAIN_CSV = "a.csv"
NGO_CSV = "ngo data.csv"

LOGIN_URL = "https://aimapp2.aim.gov.in/atl_tranche_document2019/index.php"
ATL_DOC_URL = "https://aimapp2.aim.gov.in/atl_tranche_document2019/ATL_document.php"

APPROVED_KEYWORDS = [
    "submitted successfully",
    "reviewed with approval",
    "view and print your submitted application"
]

MAX_LOGIN_ATTEMPTS = 10
MAX_ATL_WAIT = 180

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

def solve_captcha(path):
    img = cv2.imread(path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=1.8, fy=1.8)
    _, thresh = cv2.threshold(gray, 120, 255, cv2.THRESH_BINARY)

    text = pytesseract.image_to_string(
        thresh,
        config="--oem 3 --psm 7 -c tessedit_char_whitelist=abcdefghijklmnopqrstuvwxyz0123456789"
    )
    return re.sub(r"[^a-zA-Z0-9]", "", text)


def load_data():
    main_df = pd.read_csv(MAIN_CSV)
    main_df.columns = main_df.columns.str.strip().str.lower()

    required = ["atl", "email", "status", "error url", "error message"]
    for col in required:
        if col not in main_df.columns:
            raise Exception(f"Missing column in a.csv: {col}")

    if "ngo id missing" not in main_df.columns:
        main_df["ngo id missing"] = ""

    ngo_df = pd.read_csv(NGO_CSV)
    ngo_df.columns = ngo_df.columns.str.strip().str.lower()

    ngo_df.rename(columns={
        "atl code": "atl",
        "email id": "email",
        "darpan id": "darpan",
        "pan no": "pan"
    }, inplace=True)

    ngo_df = ngo_df[["email", "atl", "darpan", "pan"]]

    merged = main_df.merge(ngo_df, on=["email", "atl"], how="left")

    return main_df, merged

def auto_login(page, email, atl):
    print(f"\n▶ Login: {email} | {atl}")

    for attempt in range(1, MAX_LOGIN_ATTEMPTS + 1):
        print(f"   Attempt {attempt}/{MAX_LOGIN_ATTEMPTS}")

        try:
            page.goto(LOGIN_URL, timeout=60000)
            page.wait_for_selector("#m__Email", timeout=30000)
        except:
            time.sleep(3)
            continue

        page.fill("#m__Email", email)
        page.locator(
            "input[name='uniqueCode'], input[id*='Unique'], input[id*='Code']"
        ).first.fill(str(atl))

        page.select_option("select", label="Tranche II Portal")

        captcha_img = page.locator("#m__Captcha").locator("xpath=following::img[1]")
        captcha_img.screenshot(path="captcha.png")

        captcha = solve_captcha("captcha.png")
        if len(captcha) < 4:
            continue

        page.fill("#m__Captcha", captcha)

        try:
            page.locator("input[type='submit']").click(force=True)
        except:
            continue

        time.sleep(3)

        body = page.inner_text("body").lower()
        if "invalid captcha" in body:
            print("   ❌ Invalid captcha — retrying")
            continue

        print("   ✅ Login success")
        return True

    print("   ❌ Login failed")
    return False

def handle_ngo(page, pan, darpan):
    print("   ⚠ NGO page detected")

    page.wait_for_selector("input", timeout=30000)

    inputs = page.locator("input")
    if inputs.count() < 2:
        return False

    inputs.nth(0).fill(darpan)
    inputs.nth(1).fill(pan)

    submit = page.locator("input[type='submit'], button[type='submit']")
    if submit.count() == 0:
        return False

    print("   ▶ Submitting NGO form")

    try:
        with page.expect_navigation(timeout=60000):
            submit.first.click()
    except:
        submit.first.click()

    for _ in range(30):
        if "home.php" in page.url.lower():
            break
        time.sleep(1)

    print("   ✅ NGO submitted successfully")
    return True

def process_atl(page):
    print("   ▶ ATL flow started")

    page.goto(ATL_DOC_URL, timeout=60000)
    page.wait_for_load_state("networkidle")

    start = time.time()

    while True:
        body = page.inner_text("body").lower()

        if any(k in body for k in APPROVED_KEYWORDS):
            print("   ✅ APPROVED")
            return "APPROVED", "", ""

        submit = page.locator("button:has-text('Submit'), input[value*='Submit']")
        save = page.locator("button:has-text('Save'), input[value*='Save']")

        if submit.count() > 0:
            submit.first.click()
        elif save.count() > 0:
            save.first.click()
        else:
            if time.time() - start > MAX_ATL_WAIT:
                return "NOT APPROVED", page.url, "Stuck in ATL flow"
            time.sleep(2)
            continue

        time.sleep(3)

def logout(page):
    try:
        if page.locator("text=Logout").count() > 0:
            page.locator("text=Logout").first.click()
            time.sleep(3)
    except:
        pass


def main():
    main_df, merged_df = load_data()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=50)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()
        page.set_default_timeout(60000)

        for idx, row in merged_df.iterrows():

            if row["status"] != "NOT APPROVED":
                continue

            if "ngo id" not in str(row["error message"]).lower():
                continue

            pan = str(row["pan"]).strip()
            darpan = str(row["darpan"]).strip()

            if pan.lower() == "nan" or darpan.lower() == "nan":
                main_df.at[idx, "ngo id missing"] = "YES"
                main_df.to_csv(MAIN_CSV, index=False)
                continue

            if not auto_login(page, row["email"], row["atl"]):
                continue

            body = page.inner_text("body").lower()

            if "enter unique id of ngo" in body:
                if not handle_ngo(page, pan, darpan):
                    continue

            status, url, msg = process_atl(page)

            main_df.at[idx, "status"] = status
            main_df.at[idx, "error url"] = url
            main_df.at[idx, "error message"] = msg
            main_df.to_csv(MAIN_CSV, index=False)

            logout(page)

        browser.close()


if __name__ == "__main__":
    main()