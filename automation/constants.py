LOGIN_URL = "https://aimapp2.aim.gov.in/atl_tranche_document2019/index.php"
ATL_DOC_URL = "https://aimapp2.aim.gov.in/atl_tranche_document2019/ATL_document.php"

APPROVED_KEYWORDS = [
    "submitted successfully",
    "reviewed with approval",
    "view and print your submitted application",
]

PAGE_INFO = {
    "ATL_document1.php": {
        "step": "Step 1 - Authority Declaration",
        "reason": "Requires school principal/headmaster signature and finance officer verification",
        "pending_status": "PENDING - AUTHORITY DECLARATION NEEDED",
    },
    "ATL_document2.php": {
        "step": "Step 2 - Audited Statement of Accounts",
        "reason": "Requires audited financial statements with CA seal and signature for every financial year",
        "pending_status": "PENDING - AUDITED STATEMENT NEEDED",
    },
    "ATL_document.php": {
        "step": "Step 3 - Utilization Certificate",
        "reason": "Requires completed Utilization Certificate upload",
        "pending_status": "PENDING - UTILIZATION CERTIFICATE NEEDED",
    },
    "ATL_document4.php": {
        "step": "Step 4 - Bank Statement/Passbook",
        "reason": "Requires complete bank statements with account holder name, account number, and IFSC code visible",
        "pending_status": "PENDING - BANK STATEMENT NEEDED",
    },
    "ATL_document5.php": {
        "step": "Step 5 - Tax Exemption Declaration",
        "reason": "Requires tax exemption declaration on school letterhead with principal sign/stamp and PAN card or tax certificate",
        "pending_status": "PENDING - TAX EXEMPTION NEEDED",
    },
    "ATL_document6_moa.php": {
        "step": "Step 6 - Memorandum of Agreement",
        "reason": "Requires Supplementary MOA uploaded with school details, principal signature and stamp",
        "pending_status": "PENDING - MOA UPLOAD NEEDED",
    },
    "ATL_document_vendor.php": {
        "step": "Step 7 - Vendor/Expenditure Details",
        "reason": "Requires vendor names, invoice/bill numbers, and expenditure amounts to be filled manually in the form",
        "pending_status": "PENDING - VENDOR DETAILS NEEDED",
    },
    "ATL_document8_yt.php": {
        "step": "Step 8 - YouTube ATL Video",
        "reason": "Requires YouTube link of ATL video showing school name, students, equipment, and projects",
        "pending_status": "PENDING - YOUTUBE VIDEO NEEDED",
    },
}

ERROR_NOISE = ["(in rs)", "(in %)", "(*)", "*", "error:", "note:"]

MAX_LOGIN_ATTEMPTS = 10
MAX_ATL_WAIT = 600
