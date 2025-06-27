# stripcall for natloff
import os
import signal # Import the signal module
import logging
import re
import yaml 


from dotenv import load_dotenv
from google.cloud import secretmanager
from twilio.rest import Client
from flask import Flask, request
from google.cloud import datastore 
from flask import jsonify # Import jsonify

from twilio.twiml.messaging_response import MessagingResponse # Added a space before comment for consistency

app = Flask(__name__)
all_simulator_messages = []  # Declare all_simulator_messages at the top level
all_test_messages = []  # Declare all_test_messages at the top level
capture_active = False       # Declare global variables
current_test_case_name = None
captured_messages = []


# Configure basic logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
if not (app in locals()): 
    logger.debug("No app 1")


def access_secret_version(project_id, secret_name): 
    """
    Access the payload for the given secret version if one exists.
    """
    try:
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(name=name)
        return response.payload.data.decode("UTF-8")
    except Exception as e:
        logger.error(f"Error accessing secret {secret_name}: {e}")
    return None

@app.errorhandler(Exception)
def handle_exception(e):
    """Catch all exceptions and return a JSON error response."""
    logger.error(f"An unhandled exception occurred: {e}", exc_info=True)
    response = jsonify({"error": "An internal server error occurred."})
    response.status_code = 500
    response.headers['Content-Type'] = 'application/json'
    return response

def handle_group_command(from_number, command, parameters, datastore_client):
    command_messages = []

    phone_str = None  # Initialize phone_str
    phone_number = None # Initialize phone_number

    if len(parameters) < 2:
        command_messages.append({'to': from_number, 'body': f"Invalid syntax for +{command}. Usage: +{command} [name] [phone]"})
    else:
        name = parameters[0]
        if len(parameters) == 2:
            phone_str = parameters[1] # Correctly assign the single phone parameter
        elif len(parameters) == 3:
            # handle phone number of the form (202) 555-1212 where space is a delimiter
            phone_str = parameters[1] + " " + parameters[2] # Correctly construct the phone string from two parameters
        else:
            command_messages.append({'to': from_number, 'body': f"Invalid syntax for +{command}. Usage: +{command} [name] [phone]"})
            # If syntax is invalid, we should not attempt to parse
            phone_str = None # Ensure phone_str is None if syntax is invalid

        if phone_str: # Attempt to parse the phone number only if phone_str was successfully constructed
            phone_number = parse_phone_number(phone_str)

            if not phone_number:
                logger.debug(f"Phone number parsing failed for command: {phone_str}")
                command_messages.append({'to': from_number, 'body': f"Could not parse phone number: {phone_str}. Please use a valid format (e.g., 1234567890, +11234567890, (123) 456-7890, 123-456-7890)."})
            else:
                entity_by_name, name_found = find_entity_by_name(datastore_client, name)
                entity_by_number, number_found = find_entity_by_number(datastore_client, phone_number)

                if name_found:
                    # Name exists
                    stored_phone_number_in_name_entity = entity_by_name.get('phonNbr')
                    # Ensure stored_phone_number_in_name_entity is in a comparable format (with +1 if it's a US number)
                    if stored_phone_number_in_name_entity and len(stored_phone_number_in_name_entity) == 10 and stored_phone_number_in_name_entity.isdigit():
                        stored_phone_number_in_name_entity = '+1' + stored_phone_number_in_name_entity

                    if stored_phone_number_in_name_entity == phone_number:
                        # Name and number match an existing entry, update groups
                        entity_by_name[command] = True
                        update_user_entity(datastore_client, entity_by_name)
                        command_messages.append({'to': from_number, 'body': f"{name} with number {phone_number} is now a {command}."})
                        # Notify the user
                        command_messages.append({'to': phone_number, 'body': f"You have been added to the USA Fencing StripCall app as a {command}."})
                    else:
                        # Name exists, number differs. Check if the provided number exists elsewhere.

                        if number_found:
                            # Provided number exists with another name
                            other_name = entity_by_number.get('name')
                            command_messages.append({'to': from_number, 'body': f"Error: That telephone number is associated with {other_name}."})
                        else:
                            # Name exists, numbers differ, new number not found elsewhere. Update the existing record's phone number and groups.

                            entity_by_name[command] = True
                            entity_by_name['phonNbr'] = phone_number.lstrip('+1')
                            update_user_entity(datastore_client, entity_by_name)
                            command_messages.append({'to': from_number, 'body': f"{name} with new number {phone_number} is now a {command}."})
                            # Notify the user
                            command_messages.append({'to': phone_number, 'body': f"You have been added to the USA Fencing StripCall app as a {command}.`"})
                else:
                    # No entity found with the same name. Check if the provided number exists elsewhere.
                    if number_found:
                        # Provided number exists with another name
                        other_name = entity_by_number.get('name')
                        command_messages.append({'to': from_number, 'body': f"Error: That telephone number is associated with {other_name}."})
                    else:
                        # Neither name nor number exists. Create a new record.
                        logger.debug(f"Neither name '{name}' nor number '{phone_number}' found. Creating new entity.")
                        key = datastore_client.key('numbr')
                        new_entity = datastore.Entity(key)
                        new_entity.update({
                            'phonNbr': phone_number.lstrip('+1'),
                            'name': name,
                            'armorer': command == 'armorer',
                            'ref': False, # Assuming 'ref' is not set by these commands
                            'super': False, # Assuming 'super' is not set by these commands
                            'active': True, # Assuming new entries are active
                            'admin': False, # Assuming 'admin' is not set by these commands
                            'ucName': name.upper(), # Assuming uppercase name for ucName
                            'medic': command == 'medic',
                            'natloff': command == 'natloff'
                        })
                        update_user_entity(datastore_client, new_entity)
                        logger.debug(f"New entity created for {name} with number {phone_number}")
                        command_messages.append({'to': from_number, 'body': f"{name} with number {phone_number} is now a {command}`."})
                        command_messages.append({'to': phone_number, 'body': f"You have been added to the USA Fencing StripCall app as a {command}.`"})
    return command_messages

def handle_list_command(from_number, parameters, sender_entity, datastore_client):
    """Handles the +list command to list users in a specific group."""
    command_messages = []
    is_authorized_command_user = sender_entity and (sender_entity.get('admin', False) or sender_entity.get('super', False))

    if len(parameters) != 1:
        command_messages.append({'to': from_number, 'body': "Invalid syntax for +list. Usage: +list [group] (where group is medic, armorer, natloff, or ref)"})
    elif not is_authorized_command_user:
        command_messages.append({'to': from_number, 'body': "You are not authorized to view user lists."})
    else:
        group_filter = parameters[0].lower()
        if group_filter in ["medic", "armorer", "natloff", "ref"]:
            try:
                query = datastore_client.query(kind='numbr')
                query.add_filter(group_filter, '=', True)
                results = list(query.fetch())
                if results:
                    entries = [f"{entity.get('name', 'Unknown')} {entity.get('phonNbr', 'N/A')}" for entity in results]
                    command_messages.append({'to': from_number, 'body': f"List for {group_filter}: " + ", ".join(entries)})
                else:
                    command_messages.append({'to': from_number, 'body': f"No entries found for {group_filter}."})
            except Exception as e:
                logger.error(f"Error processing list command for group {group_filter}: {e}", exc_info=True)
                command_messages.append({'to': from_number, 'body': f'An error occurred while processing the +list command for {group_filter}.'})
        else:
            command_messages.append({'to': from_number, 'body': f"Invalid group specified: {group_filter}. Use medic, armorer, natloff, or ref."})
    return command_messages

def handle_remove_command(from_number, parameters, sender_entity, datastore_client):
    """Handles the +remove command to delete a user entity."""
    command_messages = []
    is_authorized_command_user = sender_entity and (sender_entity.get('admin', False) or sender_entity.get('super', False))

    if len(parameters) != 1:
        command_messages.append({'to': from_number, 'body': "Invalid syntax for +remove. Usage: +remove [name]"})
    elif not is_authorized_command_user:
        command_messages.append({'to': from_number, 'body': "You are not authorized to remove other users."})
    else:
        name = parameters[0]
        try:
            entity_to_delete, found = find_entity_by_name(datastore_client, name)

            if found:
                # Name exists, delete the entity
                try:
                    datastore_client.delete(entity_to_delete.key)
                    command_messages.append({'to': from_number, 'body': f"Entry for {name} has been removed."})
                    command_messages.append({'to': entity_to_delete.get('phonNbr'), 'body': f"You have been removed from the USA Fencing StripCall app. You will be re-added the next tournament you work"})
                except Exception as e:
                    logger.error(f"Error deleting entity for {name}: {e}", exc_info=True)
                    command_messages.append({'to': from_number, 'body': f"An error occurred while trying to remove the entry for {name}."})
            else:
                # Entry not found
                command_messages.append({'to': from_number, 'body': f"Entry for {name} not found."})
        except Exception as e:
             logger.error(f"Error processing remove command for {name}: {e}", exc_info=True)
             command_messages.append({'to': from_number, 'body': f'An error occurred while processing the +remove command for {name}.'})
    return command_messages

def handle_flag_status(from_number, parameters, sender_entity, datastore_client, flag_name, status):
    """Handles commands that modify a user's flag status (+activate, +deactivate, +admin, +deadmin)."""
    command_messages = []
    is_authorized_command_user = sender_entity and (sender_entity.get('admin', False) or sender_entity.get('super', False))

    action = "set " + flag_name + " to " + str(status)
    logger.debug(f"handle_flag_status parameters={parameters}, length {len(parameters)}, flag_name={flag_name}, status={status}")

    if len(parameters) == 0:
        if flag_name == 'active': # Only allow zero-parameter case for 'active' flag
            if sender_entity:
                logger.debug("modifying sender entity flag")
                try:
                    sender_entity[flag_name] = status
                    update_user_entity(datastore_client, sender_entity)
                    command_messages.append({'to': from_number, 'body': f"Your account {flag_name} status has been set to {status}."})
                except Exception as e:
                    logger.error(f"Error {action} sender: {e}", exc_info=True)
                    command_messages.append({'to': from_number, 'body': f'An error occurred while setting your {flag_name} status.'})
            else:
                command_messages.append({'to': from_number, 'body': f"Could not set your {flag_name} status. Please register first."})
        else:
            command_messages.append({'to': from_number, 'body': f"A name is required to set the {flag_name} status. Usage: +command [name]"})
    elif len(parameters) == 1:
        # Modify a named user's flag (requires admin/super)
        if not is_authorized_command_user:
            command_messages.append({'to': from_number, 'body': f"You are not authorized to set the {flag_name} status for other users."})
        else:
            try:
                name_to_modify = parameters[0]
                entity_to_modify, found = find_entity_by_name(datastore_client, name_to_modify)

                if entity_to_modify:
                    entity_to_modify[flag_name] = status
                    try:
                        update_user_entity(datastore_client, entity_to_modify)
                        command_messages.append({'to': from_number, 'body': f"{flag_name} status for {name_to_modify} has been set to {status}."})
                        command_messages.append({'to': entity_to_modify.get('phonNbr'), 'body': f"Your {flag_name} status has been set to {status}."})  
                    except Exception as db_error:
                        logger.error(f"Database error updating {flag_name} status for user {name_to_modify}: {db_error}", exc_info=True)
                        command_messages.append({'to': from_number, 'body': f'A database error occurred while setting the {flag_name} status for user {name_to_modify}.'})
                else:
                    command_messages.append({'to': from_number, 'body': f"User '{name_to_modify}' not found."})
            except Exception as e:
                logger.error(f"Error {action} user {name_to_modify}: {e}", exc_info=True)
                command_messages.append({'to': from_number, 'body': f'An error occurred while setting the {flag_name} status for user {name_to_modify}.'})
    else:
        command_messages.append({'to': from_number, 'body': f"Invalid syntax for setting {flag_name} status. Usage: +command [name]"})
    return command_messages

def handle_user_command(from_number, parameters, sender_entity, datastore_client):
# Get user details (requires admin/super)More actions
    if not is_authorized_command_user:
        command_messages.append({'to': from_number, 'body': "You are not authorized to view user details."})
    elif len(parameters) != 1:
        command_messages.append({'to': from_number, 'body': "Invalid syntax for +user. Usage: +user [name]"})
    else:
        name_to_find = parameters[0]
        user_entity, found = find_entity_by_name(datastore_client, name_to_find)
        if found:
            # Pretty-print the entity fields
            command_messages = [] # Initialize command_messages here as it's within the `if found:` block
            output_message = f"Details for {name_to_find}: "
            for key, value in user_entity.items():
                output_message += f"{key}:{value}, "
            command_messages.append({'to': from_number, 'body': output_message})
        return command_messages
    return command_messages

def update_user_entity(datastore_client, entity):
    """
    Strips the '+1' from the 'phonNbr' field if present and then puts the entity in Datastore.
    """
    if 'phonNbr' in entity and entity['phonNbr'] is not None:
        entity['phonNbr'] = entity['phonNbr'].lstrip('+1')
        datastore_client.put(entity)

def handle_capture_command(from_number, body, parameters, capture_active, current_test_case_name, captured_messages):
    """Handles the +capture command to start or stop capturing messages."""
    logger.debug(f"handle_capture_command received: body='{body}', parameters={parameters}, initial capture_active={capture_active}") # Added log

    command_parts = body.lower().split(maxsplit=2)
    command = command_parts[1] if len(command_parts) > 1 else None
    test_case_name = command_parts[2] if len(command_parts) > 2 else None

    command_messages = [] # List to hold messages to be returned by this handler
    yaml_content = None # Initialize yaml_content to None

    if command == 'start':
        if test_case_name:
            # Update the passed-in parameters directly
            capture_active = True
            current_test_case_name = test_case_name
            captured_messages = [] # Clear previous captured messages
            # Add +resetcbp interaction at the beginning of capture
            # Call handle_resetcbp_command once and add type before extending
            reset_messages = handle_resetcbp_command(from_number, datastore_client)
            # Add the 'type': 'outgoing' key to the reset messages
            for msg in reset_messages:
                msg['type'] = 'outgoing'
            command_messages.append({'to': from_number, 'body': f'Capture started for "{current_test_case_name}"'})
            reset_messages = handle_resetcbp_command(from_number, datastore_client)
            # Add the 'type': 'outgoing' key to the reset messages
            for msg in reset_messages:
                msg['type'] = 'outgoing'
            captured_messages.extend(reset_messages) # Add the reset messages to captured_messages
        else:
            command_messages.append({'to': from_number, 'body': 'Please provide a test case name with "+capture start <name>"'})
            yaml_content = None # no response in this case

    elif command == 'stop':
        # Use the passed-in capture_active
        if capture_active:
            # Update the passed-in parameter
            capture_active = False
            logger.info(f"Capture stopped for test case: {current_test_case_name}")
            # Use the passed-in current_test_case_name and captured_messages
            yaml_content = generate_yaml_from_captured_messages(current_test_case_name, captured_messages)
            command_messages.append({'to': from_number, 'body': f'Capture stopped. YAML ready.'})
            # TEMPORARY: Do NOT clear captured_messages here
            # captured_messages = []

        else:
            command_messages.append({'to': from_number, 'body': 'Capture is not active.'})

    else:
        command_messages.append({'to': from_number, 'body': 'Invalid capture command. Use "+capture start <name>" or "+capture stop"'})
        yaml_content = None

    # Return the potentially modified capture state along with messages and yaml
    return capture_active, current_test_case_name, captured_messages, command_messages, yaml_content


def handle_help_command(from_number):

    """Handles the +help command."""
    command_messages = [] # Initialize command_messages
    command_messages.append({'to': from_number, 'body': "Available commands: +help, +armorer, +medic , +natloff , +remove, +list [group], +activate, +deactivate, +admin, +deadmin, user"})
    logger.debug(f"Generated help message for {from_number}")
    return command_messages

def handle_resetcbp_command(from_number, datastore_client):
    """Handles the +resetcbp command to reset cbp for all glbvar entities."""
    command_messages = []
    for idx in range(1, 4): # Iterate through idx 1, 2, and 3
        query = datastore_client.query(kind='glbvar')
        query.add_filter('idx', '=', idx)
        results = list(query.fetch())
        if results:
            entity = results[0]
            entity['cbp'] = 1
            datastore_client.put(entity)
    command_messages.append({'to': from_number, 'body': 'cbp reset for all teams.'})
    return command_messages

# Load environment variables from .env file
load_dotenv()

# Read Twilio numbers from environment variables
ARMORER_TWILIO_NUMBER = os.getenv('ARMORER_TWILIO_NUMBER')
MEDIC_TWILIO_NUMBER = os.getenv('MEDIC_TWILIO_NUMBER')
NATLOFF_TWILIO_NUMBER = os.getenv('NATLOFF_TWILIO_NUMBER')

# Define the range of simulator numbers
SIMULATOR_NUMBER_PREFIX = os.getenv('SIMULATOR_NUMBER_PREFIX', '+1202555100')
SIMULATOR_NUMBER_START_DIGIT = int(os.getenv('SIMULATOR_NUMBER_START_DIGIT', '0'))
SIMULATOR_NUMBER_END_DIGIT = int(os.getenv('SIMULATOR_NUMBER_END_DIGIT', '9'))

def is_simulator_number(phone_number):
    """Checks if a phone number is a valid simulator number (length 12, correct prefix, last char is a digit)."""
    # Remove '+1' prefix if it exists
    cleaned_number = phone_number.lstrip('+1')
    suffix = "" # Initialize suffix to an empty string
    digit = None # Initialize digit to None

    if len(cleaned_number) == 10 and cleaned_number.startswith('202555100'):
        if phone_number.startswith(SIMULATOR_NUMBER_PREFIX):
            suffix = phone_number[len(SIMULATOR_NUMBER_PREFIX):]
        if len(suffix) == 1 and suffix.isdigit():
            digit = int(suffix)
    if digit is not None:
        return SIMULATOR_NUMBER_START_DIGIT <= digit <= SIMULATOR_NUMBER_END_DIGIT
    return False # Doesn't match the pattern


def find_entity_by_name(datastore_client, name):
    """Finds an entity by name in Datastore."""
    query = datastore_client.query(kind='numbr')
    query.add_filter('name', '=', name)
    results = list(query.fetch())
    entity = results[0] if results else None
    logger.debug(f"find_entity_by_name name={name}, entity={entity}")
    return entity, entity is not None

def find_entity_by_number(datastore_client, phone_number):
    """Finds an entity by phone number in Datastore."""
    # Datastore always has phone numbers without +1.
    cleaned_number = phone_number.lstrip('+1')
    query = datastore_client.query(kind='numbr')
    query.add_filter('phonNbr', '=', cleaned_number)
    results = list(query.fetch())
    entity = results[0] if results else None
    logger.debug(f"find_entity_by_number original={phone_number}, cleaned={cleaned_number}, results={entity}")
    return entity, entity is not None

def get_capture_state(datastore_client):
    """Retrieves the capture state from Datastore."""
    if datastore_client is None:
        logger.error("Datastore client is not initialized in get_capture_state.")
        return False, None, [] # Return default values if client is not initialized

    try:
        key = datastore_client.key('CaptureState', 'current_state')
        entity = datastore_client.get(key)
        if entity:
            logger.debug("Loaded capture state from Datastore.")
            loaded_capture_active = entity.get('capture_active', False)
            loaded_current_test_case_name = entity.get('current_test_case_name', None)
            loaded_captured_messages = entity.get('captured_messages', [])
            logger.debug(f"Loaded capture_active: {loaded_capture_active}")
            logger.debug(f"Loaded current_test_case_name: {loaded_current_test_case_name}")
            logger.debug(f"Loaded captured_messages (type: {type(loaded_captured_messages)}, count: {len(loaded_captured_messages)}): {loaded_captured_messages}") # Added detailed logging
            return loaded_capture_active, loaded_current_test_case_name, loaded_captured_messages
        else:
            logger.debug("No capture state found in Datastore. Using default values.")
            return False, None, []
    except Exception as e:
        logger.error(f"Error retrieving capture state from Datastore: {e}", exc_info=True) # Added exc_info=True
    return False, None, []


def save_capture_state(datastore_client, capture_active, current_test_case_name, captured_messages):
    """Saves the current capture state to Datastore."""
    if datastore_client is None: # Check if datastore_client is None
        logger.error("Datastore client is not initialized in save_capture_state. Cannot save capture state.")
        return # Keep this return for the case when the client is None

    try:
        key = datastore_client.key('CaptureState', 'current_state')
        entity = datastore.Entity(key)

        entity.update({
            'capture_active': capture_active,
            'current_test_case_name': current_test_case_name,
            'captured_messages': captured_messages
        })
        logger.debug(f"Saving capture_active: {capture_active}") # Added logging
        logger.debug(f"Saving current_test_case_name: {current_test_case_name}") # Added logging
        logger.debug(f"Saving captured_messages (type: {type(captured_messages)}, count: {len(captured_messages)}): {captured_messages}") # Added detailed logging

        logger.debug(f"Attempting to save capture state entity: {entity}") # Added logging before put
        datastore_client.put(entity)
        logger.debug("Saved capture state to Datastore.")
    except Exception as e:
        logger.error(f"Error saving capture state to Datastore: {e}", exc_info=True) # Added exc_info=True for full traceback



 # Define the shutdown handler
def send_single_message(to_number, body, from_number, all_simulator_messages, twilio_client, is_test_runner_request): # Modified line
    """Sends a single message to either a simulator or via Twilio."""
    if is_simulator_number(to_number):
        # Format the to_number for simulator.html (remove +1 if exists, ensure it starts with 1 if it's a simulator number)
        formatted_to_number = to_number.lstrip('+') # Remove leading + if present
        if formatted_to_number.startswith('1') and len(formatted_to_number) > 10:
             formatted_to_number = formatted_to_number[1:] # Remove the leading '1' if it's already there and part of a longer number
        formatted_to_number = '1' + formatted_to_number # Prepend '1' for consistency with HTML IDs
        formatted_from_number = from_number
        if is_simulator_number(from_number) and not from_number.startswith('+1'):
            formatted_from_number = '+1' + from_number
        message_data = {'to': to_number, 'body': body, 'from_': formatted_from_number}
        all_simulator_messages.append(message_data)
        if is_test_runner_request: 
            logger.debug(f"Test Runner Request: Adding message to all_test_messages: {message_data}")
            all_test_messages.append(message_data)
        logger.debug(f"send_single_message to simulator {to_number} from {formatted_from_number} body {body}")
        if capture_active:
            outgoing_message_data = {
                'type': 'outgoing',
                'to': to_number,
                'body': body,
                'from': from_number # Capture the original from_number passed to the function
            }
            captured_messages.append(outgoing_message_data)
            logger.debug(f"Captured outgoing message: {outgoing_message_data}")

    else:
        try:
            twilio_client.messages.create(to=to_number, body=body, from_=from_number)
            logger.debug(f"Sent message to Twilio number {to_number}: {body}")
        except Exception as e:
            logger.error(f"Error sending message via Twilio to {to_number}: {e}")


def send_message_to_group(sender_identity, sender_group, original_message, from_number, all_simulator_messages, twilio_client, is_test_runner_request):

    logger.debug(f"Attempting to send message to group: {sender_group} from sender: {sender_identity}")
    logger.debug(f"Original message to send: {original_message}")

    # Query Datastore for all entities in the sender's group
    query = datastore_client.query(kind='numbr')
    query.add_filter(sender_group, '=', True) # Assuming a boolean property for each group (medic, armorer, natloff)

    try:

        group_members = list(query.fetch())
        logger.debug(f"Found {len(group_members)} members in group {sender_group}")
        logger.debug(f"Group members are: {group_members}")
        for member in group_members:
            member_name = member.get('name')
            member_phone = member.get('phonNbr')
            member_active = member.get('active')
            # Prepend +1 if the phone number is a 10-digit number
            if member_phone and len(member_phone) == 10 and member_phone.isdigit():
                member_phone = '+1' + member_phone
            logger.debug(f"Processing member: Name: {member_name}, Phone: {member_phone}")
            # Construct the outgoing message
            outgoing_message = f"{sender_identity}: {original_message}" 
            if (member_name == sender_identity) or not(member_active):
                logger.debug(f"Skipping sending message to sender: {sender_identity}")
            else:
                send_single_message(member_phone, outgoing_message, from_number, all_simulator_messages, twilio_client, is_test_runner_request)
                logger.debug(f"Send Message to {member_phone} from {from_number} body {outgoing_message}")
    except Exception as e:
        logger.error(f"Error in send_message_to_group: {e}", exc_info=True)


# Define parse_phone_number function at the module level
def parse_phone_number(phone_str):
    """Parses and validates a phone number from a string, returning a cleaned E.164 number or None."""
    # Remove common separators (hyphens, parentheses, whitespace)
    cleaned_number = re.sub(r"[-()\s]", "", phone_str)

    if not cleaned_number:
        return None

    # Check for a leading '+'
    if cleaned_number.startswith("+"):
        # Assume international or already E.164, return as is after removing other non-digits
        # Further validation for E.164 US number (+1 followed by 10 digits)
        if cleaned_number.startswith("+1") and len(cleaned_number) == 12 and cleaned_number[1:].isdigit():
            logger.info(f"Parsed as E.164 US number: {cleaned_number}")
            return cleaned_number
        elif cleaned_number[1:].isdigit(): # It's a valid international number (starts with + and followed by digits)
            logger.info(f"Parsed as international number: {cleaned_number}")
            return cleaned_number
        else: # Starts with + but not a valid E.164 or international format
            logger.info(f"Bad phone number syntax in parse_phone_number {phone_str}")
            return None
    else:
        # Rule 2: Exactly 10 digits
        if len(cleaned_number) == 10 and cleaned_number.isdigit():
            logger.info(f"Parsed as US number: +1{cleaned_number}")
            return "+1" + cleaned_number
            # Add other formats if necessary

    return None # Didn't match any recognized format


def generate_yaml_from_captured_messages(test_case_name, captured_messages):
    """Generates YAML content from captured messages."""
    interactions = []
    expected_outgoing_messages = []
    current_incoming_message = None

    # Automatically add a +resetcbp interaction at the beginning
    interactions.append({
        'incoming_message': {
            'from': '+12025551000', # Assuming a default simulator number for commands
            'to': '+16504803067', # Assuming your app's Twilio number
            'body': '+resetcbp'
        },
        'expected_outgoing_messages': [
            {
                'to': '+12025551000',
                'body': 'cbp reset for all teams.',
                'from_': '+16504803067'
            }
        ]
    })

    for msg in captured_messages:
        if msg['type'] == 'incoming':
            # If we have collected outgoing messages for a previous incoming message,
            # finalize that interaction before starting a new one.
            if current_incoming_message is not None:
                interactions.append({
                    'incoming_message': current_incoming_message,
                    'expected_outgoing_messages': expected_outgoing_messages
                })
                expected_outgoing_messages = [] # Reset for the new interaction

            # Start a new interaction with the current incoming message
            current_incoming_message = {
                'from': msg['from'],
                'to': msg['to'],
                'body': msg['body']
            }
        elif msg['type'] == 'outgoing':
            # Add outgoing messages to the current interaction's expected outgoing list
            # if there's an active incoming message being tracked.
            if current_incoming_message is not None:
                expected_outgoing_messages.append({
                    'to': msg['to'],
                    'body': msg['body'],
                    'from_': msg['from'] # Test runner expects 'from_' key
                })
    # After the loop, add the last interaction if there's an active incoming message
    if current_incoming_message is not None:
        interactions.append({
            'incoming_message': current_incoming_message,
            'expected_outgoing_messages': expected_outgoing_messages
        })

    # Structure the final YAML data
    yaml_data = {'name': test_case_name, 'interactions': interactions}

    # Format as a YAML string
    # Use default_flow_style=None for a more readable block style
    # To try and force block style for incoming_message, we might need a custom representer if this doesn't work
    yaml_content = yaml.dump(yaml_data, allow_unicode=True, default_flow_style=False, sort_keys=False)

    return yaml_content


def shutdown_handler(signum, frame):
    """Handles termination signals to save capture state before shutting down."""
    logger.warning(f"Received signal {signum}. Shutting down gracefully.")
    # Access the global variables to save their state
    global capture_active
    global current_test_case_name
    global captured_messages
    save_capture_state(datastore_client, False, None, []) # Save with capture disabled and messages cleared
    logger.info("Capture state saved on shutdown.")

# Initialize Google Cloud Datastore client
datastore_client = None
try:
    datastore_client = datastore.Client(project=os.environ.get('DATASTORE_PROJECT_ID'))
    logger.info("Datastore client initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize Datastore client: {e}")

# Register the shutdown handler if datastore_client was initialized successfully
if datastore_client:
    signal.signal(signal.SIGTERM, shutdown_handler)
project_id = os.environ.get('GOOGLE_CLOUD_PROJECT')
twilio_account_sid_secret_name = 'twilio_account_sid'
twilio_auth_token_secret_name = 'twilio_auth_token'

twilio_account_sid = access_secret_version(project_id, twilio_account_sid_secret_name)
twilio_auth_token = access_secret_version(project_id, twilio_auth_token_secret_name)

if twilio_account_sid and twilio_auth_token:
    twilio_client = Client(twilio_account_sid, twilio_auth_token)
    logger.debug("successfully created twilio client")
else:
    logger.error("Could not retrieve Twilio credentials from Secret Manager")


@app.route('/webhook', methods=['POST'])
@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Handle incoming Twilio webhook requests.
    """
    global all_simulator_messages # Declare all_simulator_messages as global
    global all_test_messages
    global capture_active       # Declare global variables
    global current_test_case_name
    global captured_messages
    # Load capture state at the beginning of the request
    capture_active, current_test_case_name, captured_messages = get_capture_state(datastore_client)
    logger.debug(f"Loaded capture state: active={capture_active}, name={current_test_case_name}, messages={len(captured_messages)}")

    logger.debug(f"Incoming webhook request form data: {request.form}")
    logger.debug(f"capture_active at start: {capture_active}")
    is_test_runner_request = request.headers.get('X-Test-Request') == 'true' # Check for the tester header
    if is_test_runner_request: logging.debug("Test Runner Request")
    is_simulator_request = request.headers.get('X-Simulator-Request') == 'true' # Check for the simulator header
    logger.debug(f"is_simulator_request: {is_simulator_request}")
    # Determine the recipient group based on the 'To' number
    to_number = request.form.get('To')
    from_number = request.form.get('From')
    body = request.form.get('Body')
    from_group = None
    if to_number == ARMORER_TWILIO_NUMBER:
        from_group = 'armorer'
        idx = 1
    elif to_number == MEDIC_TWILIO_NUMBER:
        from_group = 'medic'
        idx = 2
    elif to_number == NATLOFF_TWILIO_NUMBER:
        from_group = 'natloff'
        idx = 3

    # Fetch glbvar entity from Datastore for non-command logic (needs to be available later)
    glbvar_entity = None
    cbp = 1 # Initialize cbp as integer, default to 1 (for 1-4 cycle)
    cb = [""] * 5 # Initialize cb as a list of 5 empty strings to match expected indices 1-4

    if datastore_client and idx is not None:
        query = datastore_client.query(kind='glbvar')
        query.add_filter('idx', '=', idx)
        results = list(query.fetch())
        if results:
            glbvar_entity = results[0]
            # Get cbp as integer, default to 1 for the 1-4 cycle
            cbp = glbvar_entity.get('cbp', 1)
            cb = glbvar_entity.get('cb', [""] * 5) # Get cb as list, default to list of 5 empty strings
            logger.debug(f"Fetched glbvar entity for idx {idx}. cbp: {cbp}, cb: {cb}")
        else:
            logger.warning(f"No glbvar entity found for idx {idx}")
    else:
        logger.error("datastore client not set or idx not set")

    logger.info(
        f"Incoming message - From: {from_number}, from_group: {from_group}, To: {to_number}, Body: {body}"
    )

    # Enhanced sender identification: prioritize searching by phonNbr, then by name
    sender_entity = None
    sender_identity = from_number
    sender_is_in_group = False # Initialize sender_is_in_group as boolean False
    sender_is_ref = False # Initialize sender_is_ref as boolean False
    is_sender_simulator = is_simulator_number(from_number)

    if datastore_client:
        # Attempt to find sender by phonNbr (original number)
        sender_entity, sender_found = find_entity_by_number(datastore_client, from_number)

        if sender_found:
            # Add +1 prefix if the phone number is a 10-digit number from Datastore
            if sender_entity.get('phonNbr') and len(sender_entity['phonNbr']) == 10 and sender_entity['phonNbr'].isdigit():
                sender_entity['phonNbr'] = '+1' + sender_entity['phonNbr']
            sender_identity = sender_entity.get('name')
            logger.debug(f"from_group={from_group} sender_group={sender_entity.get(from_group)}")
            sender_is_in_group = sender_entity.get(from_group, False) # Get group status safely
            sender_is_ref = sender_entity.get('ref', False)
            logger.debug(f"Sender identity {sender_identity} is in group={sender_is_in_group} ref={sender_is_ref}")
        # if sender_entity is not defined, then sender is a guest, and identity is from_number
        else:
            logger.debug(f"Sender number {from_number} not found")

    yaml_content_to_return = None # Initialize variable to hold YAML

    # --- Capture Mechanism: Capture incoming messages when active (for non-capture commands) ---
    # This block should be after getting message details but before any command or broadcast processing
    is_capture_command = is_simulator_request and body.lower().startswith('+capture')

    if capture_active and is_simulator_request and not is_capture_command:
        incoming_message_data = {
            'type': 'incoming',
            'from': from_number,
            'to': to_number,
            'body': body
        }
        captured_messages.append(incoming_message_data)
        logger.debug(f"Captured incoming message: {incoming_message_data}")
    # --- End Capture Mechanism: Capture incoming messages when active ---

    # The rest of the webhook function follows...
    if body.startswith("+"):
        command_parts = body[1:].split(maxsplit=1)  # Remove "+" and split into command and rest
        command = command_parts[0].lower() if command_parts else None
        parameters = command_parts[1].split() if len(command_parts) > 1 else [] # Split rest into parameters

        # Initialize a list to hold messages generated by command processing
        command_messages = []

        # Authorization check for commands
        is_authorized_command_user = sender_entity and (sender_entity.get('admin', False) or sender_entity.get('super', False))

        # This block handles all commands, with individual authorization checks
        if command == "help":
            command_messages = handle_help_command(from_number)
        elif command == "activate":
            command_messages = handle_flag_status(from_number, parameters, sender_entity, datastore_client, flag_name='active', status=True)
        elif command == "deactivate":
            command_messages = handle_flag_status(from_number, parameters, sender_entity, datastore_client, flag_name='active', status=False)
        elif command == "status":
            command_messages.append({'to': from_number, 'body': "Service is operational."})
        elif command in ["armorer", "medic", "natloff"]:
            command_messages = handle_group_command(from_number, command, parameters, datastore_client)
        elif command == "remove":
            command_messages = handle_remove_command(from_number, parameters, sender_entity, datastore_client)
        elif command == "list": # Moved list command logic to a separate function
            command_messages = handle_list_command(from_number, parameters, sender_entity, datastore_client)
        elif command == "admin":
            command_messages = handle_flag_status(from_number, parameters, sender_entity, datastore_client, flag_name='admin', status=True)
        elif command == "deadmin":
            command_messages = handle_flag_status(from_number, parameters, sender_entity, datastore_client, flag_name='admin', status=False)
        elif command == 'list':
            command_messages = handle_list_command(from_number, parameters, sender_entity, datastore_client)
        elif command == "capture":
            # Pass the capture state variables to handle_capture_command
            capture_active, current_test_case_name, captured_messages, command_messages, yaml_content_to_return = handle_capture_command(from_number, body, parameters, capture_active, current_test_case_name, captured_messages)
        elif command == "resetcbp":
            command_messages = handle_resetcbp_command(from_number, datastore_client)
        elif command in ["1", "2", "3", "4"]:
            # Check if the sender is a group member, admin, or super
            is_authorized_plus_command_user = sender_entity and (sender_entity.get(from_group, False) or sender_entity.get('admin', False) or sender_entity.get('super', False))

            if not is_authorized_plus_command_user:
                command_messages.append({'to': from_number, 'body': "You are not authorized to use this command. You must be a group member or admin"})
            else:
                try:
                    # Extract the reply number (1-4) from the command (e.g., +1, +2)
                    reply_num = int(body[1]) # Get the digit after the '+'

                    # Validate that the extracted number is between 1 and 4
                    if 1 <= reply_num <= 4:
                        # Retrieve the recipient's phone number from cb[reply_num]
                        # Ensure the index is within the bounds of the cb list, considering the 1-based index
                        if reply_num < len(cb):
                            recipient_phone_number = cb[reply_num]

                            if recipient_phone_number:
                                # Remove the command (e.g., '+1 ') from the message body
                                modified_body = body[len(command) + 1:].strip() # Remove command and leading/trailing whitespace

                                # Ensure the modified body is not empty after removing the command
                                if modified_body:
                                    logger.debug(f"Sending modified message to group and recipient from cb[{reply_num}] ({recipient_phone_number})")

                                    # Send the modified message to the group
                                    # The sender identity should be the original sender's identity
                                    send_message_to_group(sender_identity, from_group, modified_body, to_number, all_simulator_messages, twilio_client, is_test_runner_request)

                                    # Send the modified message to the recipient whose number is in cb[reply_num]
                                    # Prepend +1 if the target phone number is a 10-digit number
                                    if len(recipient_phone_number) == 10 and recipient_phone_number.isdigit():
                                        recipient_phone_number_e164 = '+1' + recipient_phone_number
                                    else:
                                        recipient_phone_number_e164 = recipient_phone_number

                                    # The from_number for the direct message to the recipient should be the sending Twilio number for the group
                                    send_single_message(recipient_phone_number_e164, modified_body, to_number, all_simulator_messages, twilio_client, is_test_runner_request)
                                else:
                                    command_messages.append({'to': from_number, 'body': "Message body is empty after removing the command."})
                            else:
                                command_messages.append({'to': from_number, 'body': f"No phone number stored for index {reply_num} in the contact list."})
                        else:
                            command_messages.append({'to': from_number, 'body': f"Error accessing contact at index {reply_num}. The contact list might be empty or shorter than expected."})

                except ValueError:
                    # This case should not happen if the command is "+1", "+2", "+3", or "+4", but included for robustness.
                    command_messages.append({'to': from_number, 'body': "Invalid command format."})
                except Exception as e:
                    logger.error(f"Error processing command {command}: {e}", exc_info=True)
                    command_messages.append({'to': from_number, 'body': "An error occurred while processing your request."})

        if command_messages:  # Only process if command generated messages
            for message in command_messages:
                send_single_message(message['to'], message['body'], to_number, all_simulator_messages, twilio_client, is_test_runner_request)
    else: # not a command, it's a broadcast
        # Ensure glbvar_entity is fetched even if it's not a command
        if datastore_client and idx is not None and glbvar_entity is None:
             # Re-fetch glbvar_entity if it wasn't found initially
             logger.warning("glbvar_entity was not found for this group initially, attempting to fetch.")
             query = datastore_client.query(kind='glbvar')
             query.add_filter('idx', '=', idx)
             results = list(query.fetch())
             if results:
                 glbvar_entity = results[0]
                 cbp = glbvar_entity.get('cbp', 1)
                 cb = glbvar_entity.get('cb', [""] * 5)
                 logger.debug(f"Re-fetched glbvar entity for idx {idx}. cbp: {cbp}, cb: {cb}")
             else:
                 logger.error(f"Could not re-fetch glbvar entity for idx {idx}")

        if not sender_is_in_group:
            if glbvar_entity: # Ensure glbvar_entity was fetched for non-commands too
                cbp = cbp + 1
                if cbp > 4:
                    cbp = 1 # Wrap around to 1
                logger.debug(f"Incremented cbp to {cbp} in non-command block")
                # Update cb list with from_number at the new cbp (which is 1-4)
                # Assuming the list 'cb' is meant to be accessed with 1-based indexing corresponding to cbp.
                cb[cbp] = from_number
                cb[cbp] = from_number.lstrip('+1')
                glbvar_entity['cbp'] = cbp
                glbvar_entity['cb'] = cb
                datastore_client.put(glbvar_entity)
                logger.debug(f"Updated glbvar entity: cbp={cbp}, cb={cb} in non-command block")
                body = body + "  +" + str(cbp) + " to reply"
            else:
                logger.error("glbvar_entity not initialized")
        send_message_to_group(sender_identity, from_group, body , to_number, all_simulator_messages, twilio_client, is_test_runner_request)
        if sender_is_ref or not sender_entity:
            send_single_message(from_number, "Got It", to_number, all_simulator_messages, twilio_client, is_test_runner_request)
    # When capture is active and the incoming message was not a capture command,
    # we need to return a non-TwiML response to the simulator's fetch request.
    logger.debug(f"capture_active at end: {capture_active}")
    save_capture_state(datastore_client, capture_active, current_test_case_name, captured_messages)
    if yaml_content_to_return is not None:
        logger.debug("Returning JSON response for captured non-command message")
        return jsonify({'status': 'capture stopped', 'yaml_content': yaml_content_to_return})
    if capture_active and is_simulator_request and not is_capture_command:
         logger.debug("Returning JSON response for captured non-command message")
         return jsonify({'status': 'message captured'})
    if is_simulator_request:
        returnVal = jsonify({'status': 'received'})
        logger.debug(f"Returning JSON response for simulator request, return={returnVal}")
        return jsonify({'status': 'received'})
    return str(MessagingResponse()) # Return TwiML response # Return TwiML response
  
@app.route('/hello_world')
def hello_world(): # Changed route to avoid conflict with '/' for simulator
    logger.debug(f"Value of datastore_client at start of hello_world: {datastore_client}")
    response_string = ""
    if datastore_client is None:
        response_string = response_string + "Could not connect to Datastore. "
        logger.error("Could not connect to Datastore")
    try:
        query = datastore_client.query(kind='numbr')
        results = list(query.fetch())
        entity = None
        for result in results:
            entity = result
            if entity.get("name") == "Brian":
                logger.info("Brian was found")
                break
    except Exception as e:
        logger.error(f"Error querying Datastore: {e}")
        return f"Error: Could not query Datastore. {e}", 500

    if entity:
        response_string = response_string + f"Hello, world! Project ID: {project_id}. Found entity with name: {entity.get('name')}, admin: {entity.get('admin')}"
        return response_string
    else:
        response_string = response_string + f"Hello, world! Project ID: {project_id}. User 'Brian' not found."
        return response_string

@app.route('/get_name', methods=['GET'])
def get_name():
    """
    Looks up a name in Datastore by phone number and returns it as JSON.
    """
    logger.debug(f"Received request for /get_name with args: {request.args}")
    phone_number = request.args.get('phoneNumber')
 
    # Initialize response_data with default values
    response_data = {
        'name': 'Unknown User',
        'groups': []
    }
    name = None
    groups = []
    found_entity = None
    
    if datastore_client and phone_number: # Check if datastore_client is initialized and phone_number is provided
        found_entity, found = find_entity_by_number(datastore_client, phone_number)

        if found:
            name = found_entity.get('name')
            # Check for group memberships
            if found_entity.get('medic', False):
                groups.append('medic')
            if found_entity.get('armorer', False):
                groups.append('armorer')
            if found_entity.get('natloff', False):
                groups.append('natloff')
            if found_entity.get('admin', False):
                groups.append('admin')
            if found_entity.get('super', False):
                groups.append('super')
            response_data = {'name': name, 'groups': groups}
            logging.debug(f"get_name returned {jsonify(response_data)}")
            return jsonify(response_data) # Return JSON response with name and groups
        else:
            logger.debug("get_name Name not found")
    response_data = {}
    # If no entity was found or phone_number was missing, return an appropriate response
    # Note: If no entity found, name remains None.
    response_data = {'name': name if found_entity else 'Unknown Neme', 'groups': groups} # Set name to 'Unknown User' if entity not found, otherwise use found name
    return jsonify(response_data)

@app.route('/get_simulator_messages', methods=['GET'])
def get_simulator_messages():
    """
    Returns messages for simulators and clears the stored messages.
    """
    global all_simulator_messages
    global all_simulator_messages
    messages_copy = all_simulator_messages[:] # Create a copy

    all_simulator_messages = [] # Clear the original list

    try:
        response = jsonify(messages_copy)
        if len(messages_copy)>0:
            logger.debug(f"get_simulator_messages returning {len(messages_copy)} messages")
        return response
    except Exception as e:
        logger.error(f"Error during jsonify or returning response: {e}", exc_info=True) # Added error logging
        # Consider returning a JSON error response here as well to avoid XML
        return jsonify({"error": "An internal error occurred while fetching messages"}), 500 # Return JSON error

@app.route('/get_test_messages', methods=['GET'])
def get_test_messages():
    """
    Returns messages for test runner and clears the stored messages.
    """
    global all_test_messages
    messages_copy = all_test_messages[:] # Create a copy
    all_test_messages = [] # Clear the original list
    if len(messages_copy)>0:
        logger.debug(f"get_test_messages returning {len(messages_copy)} messages")
    return jsonify(messages_copy)

