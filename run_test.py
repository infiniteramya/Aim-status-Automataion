"""Quick test runner: patches AimCheckNGO to use a 5-school CSV and a separate output file."""
import AimCheckNGO

# Override to use test files
AimCheckNGO.INPUT_CSV = "Test_5schools.csv"
AimCheckNGO.OUTPUT_CSV = "Test_Results.csv"

import os
# Remove old test results so we get a fresh run
if os.path.exists(AimCheckNGO.OUTPUT_CSV):
    os.remove(AimCheckNGO.OUTPUT_CSV)

AimCheckNGO.main()
