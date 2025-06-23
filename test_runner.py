import yaml
import sys
import os

# Add the parent directory to the Python path to be able to import main
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Assuming main.py is in the parent directory
try:
    import main
except ImportError:
    print("Error: main.py not found. Make sure it's in the parent directory.")
    sys.exit(1)

# Mapping for simulator abbreviations
SIMULATOR_MAP = {f'u{i}': f'+1202555100{i}' for i in range(10)}

def resolve_phone_number(identifier):
    """Resolves a phone number from an identifier (full number or simulator abbreviation)."""
    return SIMULATOR_MAP.get(identifier.lower(), identifier)

def run_test_case(test_case):
    """Runs a single test case and returns True if passed, False otherwise."""
    