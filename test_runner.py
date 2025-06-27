import yaml
import sys
import os

import re
import time

# Add the parent directory to the Python path to be able to import main
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import requests

# Define the URL of your deployed App Engine application
APP_ENGINE_URL = "https://stripcalls-458912.uk.r.appspot.com"

# Define Twilio numbers
ARMORER_TWILIO_NUMBER = "+17542276679"
MEDIC_TWILIO_NUMBER = "+13127577223"
NATLOFF_TWILIO_NUMBER = "+16504803067"

# Mapping for simulator abbreviations
SIMULATOR_NUMBERS_MAP = {f"u{i}": f"+1202555100{i}" for i in range(10)}
SIMULATOR_NUMBERS = list(SIMULATOR_NUMBERS_MAP.values())

# Mapping for Twilio number abbreviations
TWILIO_NUMBERS_MAP = {
    "armorer": ARMORER_TWILIO_NUMBER,
    "medic": MEDIC_TWILIO_NUMBER,
    "natloff": NATLOFF_TWILIO_NUMBER
}



def resolve_phone_number(identifier):
    """Resolves a phone number from an abbreviation (s0-s9, armorer, medic, natloff) or returns the number itself."""
    # Check simulator abbreviations first
    if identifier.lower() in SIMULATOR_NUMBERS_MAP:
        return SIMULATOR_NUMBERS_MAP.get(identifier.lower())
    # Check Twilio abbreviations
    elif identifier.lower() in TWILIO_NUMBERS_MAP:
        return TWILIO_NUMBERS_MAP.get(identifier.lower())
    # If not an abbreviation, return the identifier itself
    else:
        return identifier

def send_message_to_app_engine(from_number_id, to_number_id, body, retries=3, delay=2):
    """Sends an HTTP POST request to the App Engine webhook with retries.""" # Corrected docstring
    from_number = resolve_phone_number(from_number_id)
    to_number = resolve_phone_number(to_number_id)
    url = f"{APP_ENGINE_URL}/webhook"
    data = {
        'From': from_number,
        'To': to_number,
        'Body': body
    }
    headers = {'X-Test-Request': 'true'}

    for attempt in range(retries):
        try:
            response = requests.post(url, data=data, headers=headers)
            response.raise_for_status()
            return response

        except requests.exceptions.RequestException as e:
            print(f"Attempt {attempt + 1} failed to send message to App Engine: {e}")
            if attempt < retries - 1:
                print(f"Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                print("Max retries reached.")
                return None

    return None

def poll_for_expected_messages(expected_count, timeout=30, interval=1):
    """Polls the /get_test_messages endpoint until expected_count messages are received or timeout is reached."""
    start_time = time.time()
    url = f"{APP_ENGINE_URL}/get_test_messages"
    received_messages = []
    while time.time() - start_time < timeout:
        try:
            response = requests.get(url)
            response.raise_for_status()
            messages = response.json()
            if messages:
                received_messages.extend(messages)
                if len(received_messages) >= expected_count:
                    return received_messages[:expected_count] # Return exactly the expected count

        except requests.exceptions.RequestException as e:
            print(f"Error polling /get_test_messages: {e}")
        time.sleep(interval)

def get_simulator_messages():
    """Retrieves messages sent to simulator numbers from the App Engine."""
    url = f"{APP_ENGINE_URL}/get_simulator_messages"
    try:
        response = requests.get(url)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error getting simulator messages: {e}")
        return None

def run_test_case(test_case):
    """Runs a single test case and returns True if passed, False otherwise."""
    # This function now handles the new YAML format with interactions
    test_name = test_case.get('name', 'Unnamed Test Case')
    interactions = test_case.get('interactions', [])

    print(f"Running test case: {test_name}")

    if not interactions:
        print(f"  Test case '{test_name}' FAILED: No interactions defined.")
        return False

    for i, interaction in enumerate(interactions):
        print(f"  Running Interaction {i + 1}")
        incoming_message_data = interaction.get('incoming_message')
        expected_outgoing_messages_data = interaction.get('expected_outgoing_messages', [])

        if not incoming_message_data:
            print(f"  Test case '{test_name}' FAILED: Interaction {i + 1} has no incoming_message.")
            return False

        from_number_id = incoming_message_data.get('from')
        to_number_id = incoming_message_data.get('to')
        message_body = incoming_message_data.get('body')

        if not from_number_id or not to_number_id or message_body is None:
             print(f"  Test case '{test_name}' FAILED: Interaction {i + 1} has incomplete incoming_message data.")
             return False

        print(f"    Incoming: From={from_number_id}, To={to_number_id}, Body='{message_body}'")

        # Replace simulator abbreviations in the message body if necessary (assuming this is still needed)
        processed_message_body = message_body
        for abbreviation, full_number in SIMULATOR_NUMBERS_MAP.items():
             pattern = r"(^|\b)" + re.escape(abbreviation) + r"(\b|$)"
             processed_message_body = re.sub(pattern, full_number, processed_message_body)

        # Send the incoming message to the App Engine webhook
        response = send_message_to_app_engine(from_number_id, to_number_id, processed_message_body)

        if response:
            print(f"    Received response status code: {response.status_code}")
            time.sleep(0.5) # Add a small delay

            # Poll for expected outgoing messages for this interaction
            expected_count = len(expected_outgoing_messages_data)
            print(f"    Expected {expected_count} outgoing messages for this interaction.")

            received_messages = poll_for_expected_messages(expected_count)

            if received_messages is not None:
                print(f"    Received {len(received_messages)} test messages from /get_test_messages for this interaction.")

                # Prepare expected outgoing messages for comparison
                expected_messages_processed = []
                for expected_msg in expected_outgoing_messages_data:
                    expected_body = expected_msg.get('body')
                    recipient_ids = expected_msg.get('to', '').split(',')
                    for recipient_id in recipient_ids:
                        recipient_number = resolve_phone_number(recipient_id.strip())
                        expected_messages_processed.append({'to': recipient_number, 'body': expected_body, 'from_': expected_msg.get('from_')})

                # Compare received messages with expected outgoing messages for this interaction
                received_message_set = set()
                for msg in received_messages:
                    if 'body' in msg and 'from_' in msg and 'to' in msg:
                         received_message_set.add((msg['body'], msg['from_'], msg['to']))
                    else:
                         print(f"    Warning: Received message with unexpected format: {msg}")

                expected_message_set = set()
                for msg in expected_messages_processed:
                    if 'body' in msg and 'from_' in msg and 'to' in msg:
                        expected_message_set.add((msg['body'], msg['from_'], msg['to']))
                    else:
                        print(f"    Warning: Expected message with unexpected format: {msg}")


                if received_message_set == expected_message_set:
                    print(f"  Interaction {i + 1} PASSED: Messages match expected.")
                else:
                    print(f"  Interaction {i + 1} FAILED: Messages mismatch.")
                    print("      Expected messages:", expected_messages_processed)
                    print("      Received messages:", received_messages)
                    return False # Fail the test case if any interaction fails

            else:
                print(f"  Interaction {i + 1} FAILED: Could not retrieve expected messages.")
                return False

        else:
            print(f"  Test case '{test_name}' FAILED: Error sending message for Interaction {i + 1} to App Engine.")
            return False

    print(f"Test case '{test_name}' PASSED.")
    return True

def run_tests_from_yaml(yaml_file):
    """Loads test cases from a YAML file and runs them."""
    with open(yaml_file, 'r') as f:
        test_cases = yaml.safe_load(f)

    # Check if test_cases is a single dictionary (for a single test case file)
    if isinstance(test_cases, dict):
        test_cases = [test_cases] # Wrap it in a list for consistent iteration

    all_passed = True
    for test_case in test_cases:
        if not run_test_case(test_case):
            all_passed = False

    if all_passed:
        print("All test cases passed.")
    else:
        print("Some test cases failed.")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python test_runner.py <path_to_yaml_test_file>")
        sys.exit(1)
    yaml_file_path = sys.argv[1]
    run_tests_from_yaml(yaml_file_path)