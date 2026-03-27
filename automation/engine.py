import os
import re
import time
import platform
import shutil
from dataclasses import dataclass, field
from typing import Callable, Optional

import pandas as pd
import pytesseract
from playwright.sync_api import sync_playwright

from .constants import (
    LOGIN_URL,
    ATL_DOC_URL,
    APPROVED_KEYWORDS,
    PAGE_INFO,
    ERROR_NOISE,
    MAX_LOGIN_ATTEMPTS,
    MAX_ATL_WAIT,
)
from .captcha import capture_and_solve


# Configure tesseract path
if platform.system() == "Darwin":
    pytesseract.pytesseract.tesseract_cmd = (
        shutil.which("tesseract") or "/opt/homebrew/bin/tesseract"
    )
elif platform.system() == "Windows":
    pytesseract.pytesseract.tesseract_cmd = (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    )


@dataclass
class ProgressEvent:
    event: str  # "info", "login", "school_start", "school_done", "error", "done"
    school_index: int = 0
    total_schools: int = 0
    school_name: str = ""
    message: str = ""
    status: str = ""
    extra: dict = field(default_factory=dict)


class AutomationEngine:
    def __init__(
        self,
        input_csv_path: str,
        output_csv_path: str,
        on_progress: Callable[[ProgressEvent], None],
        cancel_check: Callable[[], bool],
    ):
        self.input_csv_path = input_csv_path
        self.output_csv_path = output_csv_path
        self.on_progress = on_progress
        self.cancel_check = cancel_check

    def emit(self, event: str, **kwargs):
        self.on_progress(ProgressEvent(event=event, **kwargs))

    # --- CSV handling ---

    def load_input_csv(self) -> pd.DataFrame:
        df = pd.read_csv(self.input_csv_path)
        df.columns = df.columns.str.strip().str.lower()
        df.rename(
            columns={
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
            },
            inplace=True,
        )
        required = ["atl", "email"]
        for c in required:
            if c not in df.columns:
                raise ValueError(f"Missing required column: {c}")
        return df

    def create_output_df(self, input_df: pd.DataFrame) -> pd.DataFrame:
        out_df = input_df.copy()
        out_df["status"] = ""
        out_df["stuck at step"] = ""
        out_df["error url"] = ""
        out_df["error message"] = ""
        out_df["ngo id missing"] = ""
        return out_df

    # --- Page helpers ---

    @staticmethod
    def get_step_info(url: str):
        for page_key, info in PAGE_INFO.items():
            if page_key in url:
                return info["step"], info["reason"], info["pending_status"]
        return "Unknown Step", "Page requires manual input", "NOT APPROVED"

    @staticmethod
    def _is_noise(text: str) -> bool:
        lower = text.lower().strip()
        if len(lower) < 10:
            return True
        for noise in ERROR_NOISE:
            if lower == noise or lower.strip("* ") == noise.strip("* "):
                return True
        return False

    def extract_page_issue(self, page, url: str = "") -> str:
        step_name, step_reason, _ = (
            self.get_step_info(url) if url else ("Unknown Step", "Page requires manual input", "NOT APPROVED")
        )
        parts = [step_name]
        has_detail = False

        try:
            comment = page.locator("text=Reviewer Comment").locator("xpath=following::td[1]")
            if comment.count() > 0:
                text = comment.first.inner_text().strip()
                if text and not self._is_noise(text):
                    parts.append(f"Reviewer: {text}")
                    has_detail = True
        except Exception:
            pass

        try:
            body_text = page.inner_text("body")
            for line in body_text.split("\n"):
                line = line.strip()
                if re.match(r"^\d+\.\s+(Upload|Submit|Attach|Provide)", line):
                    parts.append(f"Section: {line}")
                    has_detail = True
                    break
        except Exception:
            pass

        try:
            errors = page.locator(
                "font[color='red'], .text-danger, span[style*='red'], p[style*='red'], b[style*='red']"
            )
            for i in range(errors.count()):
                err_text = errors.nth(i).inner_text().strip()
                if err_text and not self._is_noise(err_text):
                    parts.append(f"Error: {err_text}")
                    has_detail = True
        except Exception:
            pass

        try:
            empty_fields = []
            inputs = page.locator("input[type='text'], input[type='number'], input:not([type])")
            for i in range(inputs.count()):
                inp = inputs.nth(i)
                if not inp.is_visible():
                    continue
                val = inp.input_value().strip()
                if val:
                    continue
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
                    prev_td = inp.locator("xpath=preceding::td[1]")
                    if prev_td.count() > 0:
                        field_name = prev_td.first.inner_text().strip()
                if not field_name:
                    field_name = inp_name or inp_id or "unnamed field"
                if field_name and not self._is_noise(field_name):
                    empty_fields.append(field_name)
            if empty_fields:
                parts.append(f"Empty fields: {', '.join(empty_fields[:5])}")
                has_detail = True
        except Exception:
            pass

        if not has_detail:
            parts.append(f"Reason: {step_reason}")

        return " | ".join(parts)

    # --- Login ---

    def auto_login(self, page, email: str, atl: str) -> bool:
        self.emit("login", message=f"Logging in: {email} | {atl}")

        for attempt in range(1, MAX_LOGIN_ATTEMPTS + 1):
            if self.cancel_check():
                return False

            self.emit("info", message=f"Login attempt {attempt}/{MAX_LOGIN_ATTEMPTS}")

            try:
                page.goto(LOGIN_URL, timeout=60000)
                page.wait_for_selector("#m__Email", timeout=30000)
            except Exception:
                time.sleep(3)
                continue

            page.fill("#m__Email", email)
            page.locator(
                "input[name='uniqueCode'], input[id*='Unique'], input[id*='Code']"
            ).first.fill(str(atl))
            page.select_option("select", label="Tranche II Portal")

            img = page.locator("#m__Captcha").locator("xpath=following::img[1]")
            _, captcha = capture_and_solve(img)

            if len(captcha) < 4:
                continue

            page.fill("#m__Captcha", captcha)

            try:
                page.locator("input[type='submit']").click(force=True)
            except Exception:
                continue

            time.sleep(3)
            body = page.inner_text("body").lower()

            if "invalid captcha" in body:
                self.emit("info", message="Invalid captcha — retrying")
                continue

            self.emit("info", message="Login success")
            return True

        self.emit("error", message="Login failed after max attempts")
        return False

    # --- NGO page ---

    def handle_ngo_page(self, page, pan: str, darpan: str) -> bool:
        self.emit("info", message="NGO page detected — filling PAN & DARPAN")

        try:
            page.wait_for_selector("input", timeout=30000)
        except Exception:
            return False

        inputs = page.locator("input")
        if inputs.count() < 2:
            return False

        inputs.nth(0).fill(darpan)
        inputs.nth(1).fill(pan)
        time.sleep(1)

        submit_btn = page.locator(
            "button[type='submit'], input[type='submit'], button:has-text('Submit'), input[value*='Submit']"
        )
        if submit_btn.count() == 0:
            return False

        try:
            with page.expect_navigation(timeout=60000):
                submit_btn.first.click()
        except Exception:
            submit_btn.first.click()

        self.emit("info", message="NGO form submitted")
        return True

    # --- Vendor page ---

    def try_fill_vendor_page(self, page, vendor_data: dict) -> bool:
        if not vendor_data or "ATL_document_vendor.php" not in page.url:
            return False

        vendor_name = vendor_data.get("vendor_name", "")
        invoice_no = vendor_data.get("invoice_no", "")
        vendor_amount = vendor_data.get("vendor_amount", "")

        if not any([vendor_name, invoice_no, vendor_amount]):
            return False

        self.emit("info", message="Attempting to fill vendor details from CSV")
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
        except Exception:
            pass

        if filled:
            self.emit("info", message="Vendor fields filled from CSV data")
        return filled

    # --- ATL flow ---

    def process_atl_flow(self, page, vendor_data: Optional[dict] = None):
        self.emit("info", message="ATL flow started")
        start = time.time()
        last_url = page.url
        stuck_count = 0
        vendor_attempted = False
        loop_count = 0
        last_step_reported = ""

        while True:
            loop_count += 1

            if self.cancel_check():
                return "CANCELLED", "", "Cancelled by user", ""

            elapsed = int(time.time() - start)
            if elapsed > MAX_ATL_WAIT:
                issue = self.extract_page_issue(page, page.url)
                step_name, _, pending_status = self.get_step_info(page.url)
                return pending_status, page.url, f"Timeout: {issue}", step_name

            # Report current step periodically so SSE stays alive and user sees progress
            current_step, _, _ = self.get_step_info(page.url)
            if current_step != last_step_reported:
                self.emit("info", message=f"At {current_step} ({elapsed}s elapsed)")
                last_step_reported = current_step
            elif loop_count % 5 == 0:
                self.emit("info", message=f"Still working on {current_step}... ({elapsed}s elapsed)")

            body = page.inner_text("body").lower()

            if any(k in body for k in APPROVED_KEYWORDS):
                return "APPROVED", "", "", ""

            submit = page.locator("button:has-text('Submit'), input[value*='Submit']")
            save = page.locator("button:has-text('Save'), input[value*='Save']")

            if submit.count() > 0:
                self.emit("info", message=f"Clicking Submit on {current_step}")
                submit.first.click()
            elif save.count() > 0:
                self.emit("info", message=f"Clicking Save on {current_step}")
                save.first.click()
            else:
                time.sleep(2)
                continue

            time.sleep(3)

            if page.url == last_url:
                stuck_count += 1
                if stuck_count == 1 and not vendor_attempted and vendor_data:
                    if self.try_fill_vendor_page(page, vendor_data):
                        vendor_attempted = True
                        stuck_count = 0
                        continue
                if stuck_count >= 3:
                    issue = self.extract_page_issue(page, page.url)
                    step_name, _, pending_status = self.get_step_info(page.url)
                    return pending_status, page.url, issue, step_name
            else:
                self.emit("info", message=f"Progressed to: {page.url.split('/')[-1]}")
                last_url = page.url
                stuck_count = 0

    def logout(self, page):
        try:
            if page.locator("text=Logout").count() > 0:
                page.locator("text=Logout").first.click()
                time.sleep(3)
        except Exception:
            pass

    # --- Main run ---

    def run(self):
        self.emit("info", message="Loading CSV...")
        input_df = self.load_input_csv()
        out_df = self.create_output_df(input_df)
        total = len(out_df)

        self.emit("info", message=f"Loaded {total} schools. Starting browser...")

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()
            page.set_default_timeout(60000)

            try:
                for idx, row in out_df.iterrows():
                    if self.cancel_check():
                        self.emit("info", message="Job cancelled by user")
                        break

                    school_name = str(row.get("school_name", f"School {idx + 1}")).strip()
                    email = str(row["email"]).strip()
                    atl = str(row["atl"]).strip()
                    pan = str(row.get("pan", "")).strip()
                    darpan = str(row.get("darpan", "")).strip()
                    has_ngo_data = not (
                        pan.lower() == "nan" or darpan.lower() == "nan" or pan == "" or darpan == ""
                    )

                    self.emit(
                        "school_start",
                        school_index=idx + 1,
                        total_schools=total,
                        school_name=school_name,
                        message=f"Processing {school_name} ({idx + 1}/{total})",
                    )

                    if not self.auto_login(page, email, atl):
                        out_df.at[idx, "status"] = "LOGIN FAILED"
                        out_df.at[idx, "error message"] = "Could not login after max attempts"
                        out_df.to_csv(self.output_csv_path, index=False)
                        self.emit(
                            "school_done",
                            school_index=idx + 1,
                            total_schools=total,
                            school_name=school_name,
                            status="LOGIN FAILED",
                            message=f"{school_name}: LOGIN FAILED",
                        )
                        continue

                    body = page.inner_text("body").lower()

                    if "enter unique id of ngo" in body or "enter pan number" in body:
                        if has_ngo_data:
                            if not self.handle_ngo_page(page, pan, darpan):
                                out_df.at[idx, "status"] = "NGO FORM FAILED"
                                out_df.at[idx, "error url"] = page.url
                                out_df.at[idx, "error message"] = "Failed to submit NGO form"
                                out_df.to_csv(self.output_csv_path, index=False)
                                self.logout(page)
                                self.emit(
                                    "school_done",
                                    school_index=idx + 1,
                                    total_schools=total,
                                    school_name=school_name,
                                    status="NGO FORM FAILED",
                                )
                                continue
                        else:
                            out_df.at[idx, "status"] = "NGO ID MISSING"
                            out_df.at[idx, "ngo id missing"] = "YES"
                            out_df.at[idx, "error url"] = page.url
                            out_df.at[idx, "error message"] = (
                                "Website requires DARPAN ID & PAN but not available in data"
                            )
                            out_df.to_csv(self.output_csv_path, index=False)
                            self.logout(page)
                            self.emit(
                                "school_done",
                                school_index=idx + 1,
                                total_schools=total,
                                school_name=school_name,
                                status="NGO ID MISSING",
                            )
                            continue

                    for _ in range(30):
                        if "home.php" in page.url.lower():
                            break
                        time.sleep(1)

                    vendor_data = {}
                    for col in ["vendor_name", "invoice_no", "vendor_amount"]:
                        val = str(row.get(col, "")).strip()
                        if val and val.lower() != "nan":
                            vendor_data[col] = val

                    self.emit("info", message="Redirecting to ATL document page")
                    page.goto(ATL_DOC_URL, timeout=60000)
                    page.wait_for_load_state("networkidle")

                    status, url, msg, step = self.process_atl_flow(page, vendor_data or None)

                    out_df.at[idx, "status"] = status
                    out_df.at[idx, "stuck at step"] = step
                    out_df.at[idx, "error url"] = url
                    out_df.at[idx, "error message"] = msg
                    out_df.to_csv(self.output_csv_path, index=False)

                    self.emit(
                        "school_done",
                        school_index=idx + 1,
                        total_schools=total,
                        school_name=school_name,
                        status=status,
                        message=f"{school_name}: {status}",
                    )

                    self.logout(page)
            finally:
                browser.close()

        self.emit("done", message=f"Completed! Results saved to {self.output_csv_path}")
