import time
import re
import platform
import shutil
import os
import pandas as pd
import pytesseract
import cv2

from playwright.sync_api import sync_playwright, TimeoutError

INPUT_CSV = "Testing Data Sheet1.csv"
OUTPUT_CSV = "Results.csv"

LOGIN_URL = "https://aimapp2.aim.gov.in/atl_tranche_document2019/index.php"
ATL_DOC_URL = "https://aimapp2.aim.gov.in/atl_tranche_document2019/ATL_document.php"

APPROVED_KEYWORDS = [
    "submitted successfully",
    "reviewed with approval",
    "view and print your submitted application"
]

PAGE_INFO = {
    "ATL_document1.php": {
        "step": "Step 1 - Authority Declaration",
        "reason": "Requires school principal/headmaster signature and finance officer verification",
        "pending_status": "PENDING - AUTHORITY DECLARATION NEEDED"
    },
    "ATL_document2.php": {
        "step": "Step 2 - Audited Statement of Accounts",
        "reason": "Requires audited financial statements with CA seal and signature for every financial year",
        "pending_status": "PENDING - AUDITED STATEMENT NEEDED"
    },
    "ATL_document.php": {
        "step": "Step 3 - Utilization Certificate",
        "reason": "Requires completed Utilization Certificate upload",
        "pending_status": "PENDING - UTILIZATION CERTIFICATE NEEDED"
    },
    "ATL_document4.php": {
        "step": "Step 4 - Bank Statement/Passbook",
        "reason": "Requires complete bank statements with account holder name, account number, and IFSC code visible",
        "pending_status": "PENDING - BANK STATEMENT NEEDED"
    },
    "ATL_document5.php": {
        "step": "Step 5 - Tax Exemption Declaration",
        "reason": "Requires tax exemption declaration on school letterhead with principal sign/stamp and PAN card or tax certificate",
        "pending_status": "PENDING - TAX EXEMPTION NEEDED"
    },
    "ATL_document6_moa.php": {
        "step": "Step 6 - Memorandum of Agreement",
        "reason": "Requires Supplementary MOA uploaded with school details, principal signature and stamp",
        "pending_status": "PENDING - MOA UPLOAD NEEDED"
    },
    "ATL_document_vendor.php": {
        "step": "Step 7 - Vendor/Expenditure Details",
        "reason": "Requires vendor names, invoice/bill numbers, and expenditure amounts to be filled manually in the form",
        "pending_status": "PENDING - VENDOR DETAILS NEEDED"
    },
    "ATL_document8_yt.php": {
        "step": "Step 8 - YouTube ATL Video",
        "reason": "Requires YouTube link of ATL video showing school name, students, equipment, and projects",
        "pending_status": "PENDING - YOUTUBE VIDEO NEEDED"
    }
}

ERROR_NOISE = ["(in rs)", "(in %)", "(*)", "*", "error:", "note:"]


def get_step_info(url):
    for page_key, info in PAGE_INFO.items():
        if page_key in url:
            return info["step"], info["reason"], info["pending_status"]
    return "Unknown Step", "Page requires manual input", "NOT APPROVED"


MAX_LOGIN_ATTEMPTS = 10
MAX_ATL_WAIT = 600

if platform.system() == "Darwin":
    pytesseract.pytesseract.tesseract_cmd = shutil.which("tesseract") or "/opt/homebrew/bin/tesseract"
elif platform.system() == "Windows":
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


def load_input_csv():
    df = pd.read_csv(INPUT_CSV)
    df.columns = df.columns.str.strip().str.lower()

    df.rename(columns={
        "s. no": "s_no",
        "school name": "school_name",
        "atl code": "atl",
        "email id": "email",
        "darpan id": "darpan",
        "pan no": "pan",
        "vendor name": "vendor_name",
        "invoice no": "invoice_no",
        "invoice number": "invoice_no",
        "bill no": "invoice_no",
        "expenditure amount": "vendor_amount",
        "amount": "vendor_amount",
    }, inplace=True)

    required = ["atl", "email"]
    for c in required:
        if c not in df.columns:
            raise Exception(f"Missing column in {INPUT_CSV}: {c}")

    return df


def load_or_create_output(input_df):
    if os.path.exists(OUTPUT_CSV):
        out_df = pd.read_csv(OUTPUT_CSV)
        out_df.columns = out_df.columns.str.strip().str.lower()
        if "stuck at step" not in out_df.columns:
            out_df.insert(out_df.columns.get_loc("error url"), "stuck at step", "")
        return out_df

    out_df = input_df.copy()
    out_df["status"] = ""
    out_df["stuck at step"] = ""
    out_df["error url"] = ""
    out_df["error message"] = ""
    out_df["ngo id missing"] = ""
    out_df.to_csv(OUTPUT_CSV, index=False)
    return out_df


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

        img = page.locator("#m__Captcha").locator("xpath=following::img[1]")
        img.screenshot(path="captcha.png")

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

    print("   ❌ Login failed completely")
    return False


def handle_ngo_page(page, pan, darpan):
    print("   ⚠ NGO page detected → filling PAN & DARPAN")

    try:
        page.wait_for_selector("input", timeout=30000)
    except:
        print("   ❌ NGO form not loaded")
        return False

    inputs = page.locator("input")

    if inputs.count() < 2:
        print("   ❌ NGO inputs not found")
        return False

    inputs.nth(0).fill(darpan)
    inputs.nth(1).fill(pan)

    time.sleep(1)

    submit_btn = page.locator(
        "button[type='submit'], input[type='submit'], button:has-text('Submit'), input[value*='Submit']"
    )

    if submit_btn.count() == 0:
        print("   ❌ NGO submit button not found")
        return False

    print("   ▶ Clicking NGO Submit")

    try:
        with page.expect_navigation(timeout=60000):
            submit_btn.first.click()
    except:
        submit_btn.first.click()

    print("   ✅ NGO submitted")

    return True


def try_fill_vendor_page(page, vendor_data):
    """Attempt to fill vendor/expenditure fields on ATL_document_vendor.php if data is available."""
    if not vendor_data or "ATL_document_vendor.php" not in page.url:
        return False

    vendor_name = vendor_data.get("vendor_name", "")
    invoice_no = vendor_data.get("invoice_no", "")
    vendor_amount = vendor_data.get("vendor_amount", "")

    if not any([vendor_name, invoice_no, vendor_amount]):
        return False

    print("   ▶ Attempting to fill vendor details from CSV")
    filled = False

    try:
        inputs = page.locator("input[type='text'], input[type='number'], input:not([type])")
        for i in range(inputs.count()):
            inp = inputs.nth(i)
            if not inp.is_visible() or inp.input_value().strip():
                continue
            name = (inp.get_attribute("name") or "").lower()
            placeholder = (inp.get_attribute("placeholder") or "").lower()
            hint = name + " " + placeholder

            if vendor_name and any(k in hint for k in ["vendor", "supplier", "party"]):
                inp.fill(str(vendor_name))
                filled = True
            elif invoice_no and any(k in hint for k in ["invoice", "bill", "voucher"]):
                inp.fill(str(invoice_no))
                filled = True
            elif vendor_amount and any(k in hint for k in ["amount", "expenditure", "total", "rs"]):
                inp.fill(str(vendor_amount))
                filled = True
    except:
        pass

    if filled:
        print("   ✅ Vendor fields filled from CSV data")
    else:
        print("   ⚠ Vendor columns found in CSV but could not match to form fields")

    return filled


def _is_noise(text):
    """Return True if the text is a known junk/noise string from page elements."""
    lower = text.lower().strip()
    if len(lower) < 10:
        return True
    for noise in ERROR_NOISE:
        if lower == noise or lower.strip("* ") == noise.strip("* "):
            return True
    return False


def extract_page_issue(page, url=""):
    """Scrape the actual issue from the page and prefix with step info from PAGE_INFO."""
    step_name, step_reason, _ = get_step_info(url) if url else ("Unknown Step", "Page requires manual input", "NOT APPROVED")
    parts = [step_name]
    has_detail = False

    # Extract reviewer comment
    try:
        comment = page.locator("text=Reviewer Comment").locator("xpath=following::td[1]")
        if comment.count() > 0:
            text = comment.first.inner_text().strip()
            if text and not _is_noise(text):
                parts.append(f"Reviewer: {text}")
                has_detail = True
    except:
        pass

    # Extract section heading (e.g. "4. Upload Bank Statement/Passbook...")
    try:
        body_text = page.inner_text("body")
        for line in body_text.split("\n"):
            line = line.strip()
            if re.match(r"^\d+\.\s+(Upload|Submit|Attach|Provide)", line):
                parts.append(f"Section: {line}")
                has_detail = True
                break
    except:
        pass

    # Extract red error messages, filtering out noise like "(In Rs)"
    try:
        errors = page.locator("font[color='red'], .text-danger, span[style*='red'], p[style*='red'], b[style*='red']")
        for i in range(errors.count()):
            err_text = errors.nth(i).inner_text().strip()
            if err_text and not _is_noise(err_text):
                parts.append(f"Error: {err_text}")
                has_detail = True
    except:
        pass

    # Extract empty/unfilled form field names to show what exactly needs filling
    try:
        empty_fields = []
        # Check text inputs that are empty
        inputs = page.locator("input[type='text'], input[type='number'], input:not([type])")
        for i in range(inputs.count()):
            inp = inputs.nth(i)
            if not inp.is_visible():
                continue
            val = inp.input_value().strip()
            if val:
                continue
            # Try to find a label for this input
            field_name = ""
            inp_id = inp.get_attribute("id") or ""
            inp_name = inp.get_attribute("name") or ""
            placeholder = inp.get_attribute("placeholder") or ""
            if inp_id:
                label = page.locator(f"label[for='{inp_id}']")
                if label.count() > 0:
                    field_name = label.first.inner_text().strip()
            if not field_name and placeholder:
                field_name = placeholder
            if not field_name:
                # Try the nearest preceding td/th/label text
                prev_td = inp.locator("xpath=preceding::td[1]")
                if prev_td.count() > 0:
                    field_name = prev_td.first.inner_text().strip()
            if not field_name:
                field_name = inp_name or inp_id or "unnamed field"
            if field_name and not _is_noise(field_name):
                empty_fields.append(field_name)
        if empty_fields:
            parts.append(f"Empty fields: {', '.join(empty_fields[:5])}")
            has_detail = True
    except:
        pass

    # If no meaningful detail was found, append the step reason as explanation
    if not has_detail:
        parts.append(f"Reason: {step_reason}")

    return " | ".join(parts)


def process_atl_flow(page, vendor_data=None):
    print("   ▶ ATL flow started")
    start = time.time()
    last_url = page.url
    stuck_count = 0
    vendor_attempted = False

    while True:
        if time.time() - start > MAX_ATL_WAIT:
            issue = extract_page_issue(page, page.url)
            step_name, _, pending_status = get_step_info(page.url)
            print(f"   ❌ Timeout at {step_name} — {issue}")
            return pending_status, page.url, f"Timeout: {issue}", step_name

        body = page.inner_text("body").lower()

        if any(k in body for k in APPROVED_KEYWORDS):
            print("   ✅ APPROVED")
            return "APPROVED", "", "", ""

        submit = page.locator("button:has-text('Submit'), input[value*='Submit']")
        save = page.locator("button:has-text('Save'), input[value*='Save']")

        if submit.count() > 0:
            submit.first.click()
        elif save.count() > 0:
            save.first.click()
        else:
            time.sleep(2)
            continue

        time.sleep(3)

        if page.url == last_url:
            stuck_count += 1
            # On first sign of being stuck at vendor page, try auto-filling
            if stuck_count == 1 and not vendor_attempted and vendor_data:
                if try_fill_vendor_page(page, vendor_data):
                    vendor_attempted = True
                    stuck_count = 0
                    continue
            if stuck_count >= 3:
                issue = extract_page_issue(page, page.url)
                step_name, _, pending_status = get_step_info(page.url)
                print(f"   ❌ Stuck at {step_name} — {issue}")
                return pending_status, page.url, issue, step_name
        else:
            last_url = page.url
            stuck_count = 0

def logout(page):
    try:
        if page.locator("text=Logout").count() > 0:
            page.locator("text=Logout").first.click()
            time.sleep(3)
    except:
        pass

def main():
    input_df = load_input_csv()
    out_df = load_or_create_output(input_df)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            slow_mo=50,
            args=["--disable-blink-features=AutomationControlled"]
        )

        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()
        page.set_default_timeout(60000)

        for idx, row in out_df.iterrows():

            if str(row.get("status", "")).strip() != "":
                print(f"\n⏭ Skipping row {idx + 1} — already processed ({row['status']})")
                continue

            email = str(row["email"]).strip()
            atl = str(row["atl"]).strip()
            pan = str(row.get("pan", "")).strip()
            darpan = str(row.get("darpan", "")).strip()
            has_ngo_data = not (pan.lower() == "nan" or darpan.lower() == "nan" or pan == "" or darpan == "")

            if not auto_login(page, email, atl):
                out_df.at[idx, "status"] = "LOGIN FAILED"
                out_df.at[idx, "error message"] = "Could not login after max attempts"
                out_df.to_csv(OUTPUT_CSV, index=False)
                continue

            body = page.inner_text("body").lower()

            if "enter unique id of ngo" in body or "enter pan number" in body:
                if has_ngo_data:
                    if not handle_ngo_page(page, pan, darpan):
                        out_df.at[idx, "status"] = "NGO FORM FAILED"
                        out_df.at[idx, "error url"] = page.url
                        out_df.at[idx, "error message"] = "Failed to submit NGO form"
                        out_df.to_csv(OUTPUT_CSV, index=False)
                        logout(page)
                        continue
                else:
                    out_df.at[idx, "status"] = "NGO ID MISSING"
                    out_df.at[idx, "ngo id missing"] = "YES"
                    out_df.at[idx, "error url"] = page.url
                    out_df.at[idx, "error message"] = "Website requires DARPAN ID & PAN but not available in data"
                    out_df.to_csv(OUTPUT_CSV, index=False)
                    logout(page)
                    continue

            for _ in range(30):
                if "home.php" in page.url.lower():
                    break
                time.sleep(1)

            # Gather vendor data from CSV if columns exist
            vendor_data = {}
            for col in ["vendor_name", "invoice_no", "vendor_amount"]:
                val = str(row.get(col, "")).strip()
                if val and val.lower() != "nan":
                    vendor_data[col] = val

            print("   ▶ Redirecting to ATL document page")
            page.goto(ATL_DOC_URL, timeout=60000)
            page.wait_for_load_state("networkidle")

            status, url, msg, step = process_atl_flow(page, vendor_data or None)

            out_df.at[idx, "status"] = status
            out_df.at[idx, "stuck at step"] = step
            out_df.at[idx, "error url"] = url
            out_df.at[idx, "error message"] = msg
            out_df.to_csv(OUTPUT_CSV, index=False)

            logout(page)

        browser.close()

    print(f"\n✅ Done! Results saved to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
