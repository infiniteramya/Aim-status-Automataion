import re
import tempfile
import cv2
import pytesseract


def solve_captcha(image_path: str) -> str:
    img = cv2.imread(image_path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=1.8, fy=1.8)
    _, thresh = cv2.threshold(gray, 120, 255, cv2.THRESH_BINARY)

    text = pytesseract.image_to_string(
        thresh,
        config="--oem 3 --psm 7 -c tessedit_char_whitelist=abcdefghijklmnopqrstuvwxyz0123456789",
    )
    return re.sub(r"[^a-zA-Z0-9]", "", text)


def capture_and_solve(locator) -> tuple[str, str]:
    """Screenshot a captcha element to a temp file, solve it, return (path, text)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    locator.screenshot(path=tmp.name)
    text = solve_captcha(tmp.name)
    return tmp.name, text
